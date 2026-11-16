#!/usr/bin/env python3
"""Action extraction from Spiky reports -> _Agent-Context/TASKS.md (worker).

Feeds new Inbox/Spiky notes to Claude; ONLY the owner's own promises (the
"promises" section) and work delegated to others (the "waiting" section) are
extracted. Section headings come from tools/locale/<lang>/spiky_actions.json.
Row format (gtasks_sync.py parses this):
  - [ ] Task text | [[Meeting note name]] | YYYY-MM-DD
  - [ ] Person: Task text | [[...]] | YYYY-MM-DD   (waiting section)

State: .agents/state/spiky_actions_done (processed file names).
--backfill N: processes the reports of the last N days (for first setup).
"""
import argparse
import os
import re
from datetime import datetime, timedelta

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
from llm import run_prompt
from owner_profile import OWNER, OWNER_FULL, WORKER, output_lang_directive
from i18n import t

SPIKY_DIR = os.path.join(VAULT, "Inbox", "Spiky")
TASKS_FILE = os.path.join(VAULT, "_Agent-Context", "TASKS.md")
STATE_FILE = os.path.join(VAULT, ".agents", "state", "spiky_actions_done")
CONTEXT_FILE = os.path.join(VAULT, "_Agent-Context", "CONTEXT.md")
MAX_PER_RUN = 10

SECTION_PROMISES = t("spiky_actions.section_promises")
SECTION_WAITING = t("spiky_actions.section_waiting")
HEADER = (t("spiky_actions.tasks_header", worker=WORKER) + "\n\n"
          + SECTION_PROMISES + "\n\n" + SECTION_WAITING + "\n")


from logline import log
import tasks_ledger


def read_file(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def note_date(fname):
    m = re.match(r"(\d{4}-\d{2}-\d{2})", fname)
    return m.group(1) if m else None


def open_task_titles(tasks_md):
    return [m.group(1).split(" | ")[0].strip()
            for m in re.finditer(r"- \[ \] (.+)", tasks_md or "")]


# The near-duplicate guard lives in tools/task_dedup.py since 2026-09-19, so
# the nightly digest applies the same one; the names stay importable here.
from task_dedup import DUPLICATE_OVERLAP, STEM_LEN, _words, is_duplicate  # noqa: F401


def extract(note_name, note_text, existing):
    context = (read_file(CONTEXT_FILE) or "")[:3000]
    existing_block = "\n".join(f"- {x}" for x in existing[:60]) or "(empty)"
    prompt = f"""You are an extraction assistant feeding the task ledger of {OWNER}.
From the Spiky meeting report below extract ONLY the following:
1. "me": work the owner {OWNER} took on personally (appears in reports as "{OWNER_FULL}: ...").
2. "delegated": work the owner {OWNER} handed to / is waiting on from someone else; with the person's name.

RULES:
- Skip items that do not concern the owner {OWNER} (including work between two other people).
- Skip vague/general items (ownerless phrases like "the situation will be reviewed").
- Fix transcription errors in names from context.
- Do NOT REPEAT items that are the same as / very similar to those ALREADY IN THE LEDGER.
- Keep the task text short and action-oriented (at most ~12 words).
- Return ONLY valid JSON, write nothing else. Schema:
[{{"owner": "me" or "delegated", "person": "person name if delegated, else empty", "task": "..."}}]
- Return [] when there is nothing to extract.
- Task text and person names: {output_lang_directive()}

# CURRENT LEDGER (do not repeat):
{existing_block}

# CONTEXT FOR {OWNER} (for name corrections):
{context}

# REPORT ({note_name}):
{note_text[:9000]}"""
    out = run_prompt(prompt, timeout=180, lane="spiky-actions")
    if not out:
        return []
    from llm import extract_json
    items = extract_json(out, list)
    if items is None:
        log(f"{note_name}: JSON could not be parsed")
        return []
    return [i for i in items if isinstance(i, dict) and i.get("task")]


def _clean_field(s, limit=140):
    """Flatten an LLM field to one line: cut newline/pipe/[[ ]] injection.
    Otherwise the TASKS.md parser, Google Tasks and Telegram can be poisoned."""
    s = re.sub(r"[\r\n\t]+", " ", str(s))
    s = s.replace("|", "/").replace("[[", "(").replace("]]", ")")
    return re.sub(r"\s{2,}", " ", s).strip()[:limit]


def append_tasks(ledger, items, note_name, date_str):
    """Add the extracted items to an open tasks_ledger.Ledger. Returns how many went in."""
    link = os.path.splitext(note_name)[0]
    n = 0
    for item in items:
        task = _clean_field(item["task"])
        if item.get("owner") == "delegated" and item.get("person"):
            text, key = f"{_clean_field(item['person'], 40)}: {task}", "waiting"
        else:
            text, key = task, "promises"
        n += ledger.add(key, text, link, date_str, include_done=False)
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", type=int, default=0, metavar="DAYS")
    args = ap.parse_args()

    done = set((read_file(STATE_FILE) or "").splitlines())
    cutoff = None
    if args.backfill:
        cutoff = (datetime.now() - timedelta(days=args.backfill)).strftime("%Y-%m-%d")

    candidates = []
    for f in sorted(os.listdir(SPIKY_DIR) if os.path.isdir(SPIKY_DIR) else []):
        if not f.endswith(".md") or f in done:
            continue
        d = note_date(f)
        if cutoff and (not d or d < cutoff):
            done.add(f)  # outside the backfill window: count as processed
            continue
        if not args.backfill and (not d or d < (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")):
            done.add(f)  # in normal mode skip new-looking files older than 7 days
            continue
        candidates.append(f)

    for f in candidates[:MAX_PER_RUN]:
        note = read_file(os.path.join(SPIKY_DIR, f)) or ""
        # The model sees the open titles so it does not propose them again; the
        # ledger's own guard decides at write time, under the lock, so two
        # writers cannot both add the same promise.
        items = extract(f, note, tasks_ledger.read(TASKS_FILE).titles(include_done=False))
        if items:
            with tasks_ledger.edit(TASKS_FILE) as ledger:
                n = append_tasks(ledger, items, f, note_date(f) or "")
            log(f"{f}: {n} item(s)" + (f", {len(items) - n} duplicate(s) dropped" if n < len(items) else ""))
        else:
            log(f"{f}: no items")
        done.add(f)

    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        fh.write("\n".join(sorted(done)))
    if len(candidates) > MAX_PER_RUN:
        log(f"{len(candidates) - MAX_PER_RUN} file(s) left for the next run")


if __name__ == "__main__":
    main()
