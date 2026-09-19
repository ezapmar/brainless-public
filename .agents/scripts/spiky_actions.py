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
import json
import os
import re
import sys
from datetime import datetime, timedelta

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
sys.path.insert(0, os.path.join(VAULT, "tools"))
from llm import run_prompt
from owner_profile import OWNER, OWNER_FULL, WORKER, output_lang_directive  # noqa: E402
from i18n import t, t_list  # noqa: E402

SPIKY_DIR = os.path.join(VAULT, "Inbox", "Spiky")
TASKS_FILE = os.path.join(VAULT, "_Agent-Context", "TASKS.md")
STATE_FILE = os.path.join(VAULT, ".agents", "state", "spiky_actions_done")
CONTEXT_FILE = os.path.join(VAULT, "_Agent-Context", "CONTEXT.md")
MAX_PER_RUN = 10

SECTION_PROMISES = t("spiky_actions.section_promises")
SECTION_WAITING = t("spiky_actions.section_waiting")
HEADER = (t("spiky_actions.tasks_header", worker=WORKER) + "\n\n"
          + SECTION_PROMISES + "\n\n" + SECTION_WAITING + "\n")


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def read_file(path):
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return None


def note_date(fname):
    m = re.match(r"(\d{4}-\d{2}-\d{2})", fname)
    return m.group(1) if m else None


def open_task_titles(tasks_md):
    return [m.group(1).split(" | ")[0].strip()
            for m in re.finditer(r"- \[ \] (.+)", tasks_md or "")]


# Words that carry no meaning for "is this the same task", in both languages of
# the vault. Without them "Ozan ile konus" and "Ozan ile konusulacak" score as
# different tasks and the ledger grows a near-duplicate every week.
_STOP = {"ve", "ile", "icin", "bir", "bu", "su", "o", "de", "da", "ki", "mi",
         "the", "a", "an", "to", "for", "of", "and", "with", "on", "in", "is"}
DUPLICATE_OVERLAP = 0.6


STEM_LEN = 5


def _words(text):
    """Lower-case content stems, Turkish characters folded.

    Two foldings, both needed. Diacritics go because a transcript and a model
    disagree about them. Words are then cut to their first five characters,
    because Turkish is agglutinative: "entegrasyonu" and "entegrasyonunu" are
    the same word wearing different suffixes, and whole-word matching would
    call the same task new every week.
    """
    folded = (text or "").lower()
    for a, b in (("ı", "i"), ("ş", "s"), ("ğ", "g"), ("ü", "u"), ("ö", "o"), ("ç", "c")):
        folded = folded.replace(a, b)
    return {w[:STEM_LEN] for w in re.findall(r"[a-z0-9]+", folded)
            if w not in _STOP and len(w) > 2}


def is_duplicate(task, existing):
    """True when the ledger already holds this task in different words.

    The prompt asks the model not to repeat itself, and a large model obeys.
    A small local one does not always, and a duplicated promise is worse than
    a missed one: it reaches Google Tasks and then a person. So the guard is
    deterministic and sits after the model, where instructions cannot reach it.
    """
    new = _words(task)
    if not new:
        return True  # nothing but stop words is not a task
    for old in existing:
        prev = _words(old)
        if not prev:
            continue
        overlap = len(new & prev) / min(len(new), len(prev))
        if overlap >= DUPLICATE_OVERLAP:
            return True
    return False


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
    m = re.search(r"\[.*\]", out, re.S)
    if not m:
        return []
    try:
        items = json.loads(m.group(0))
    except ValueError:
        log(f"{note_name}: JSON could not be parsed")
        return []
    return [i for i in items if isinstance(i, dict) and i.get("task")]


def _clean_field(s, limit=140):
    """Flatten an LLM field to one line: cut newline/pipe/[[ ]] injection.
    Otherwise the TASKS.md parser, Google Tasks and Telegram can be poisoned."""
    s = re.sub(r"[\r\n\t]+", " ", str(s))
    s = s.replace("|", "/").replace("[[", "(").replace("]]", ")")
    return re.sub(r"\s{2,}", " ", s).strip()[:limit]


def append_tasks(tasks_md, items, note_name, date_str):
    lines = tasks_md.splitlines(keepends=True)
    link = os.path.splitext(note_name)[0]
    for item in items:
        task = _clean_field(item["task"])
        if item.get("owner") == "delegated" and item.get("person"):
            text = f"{_clean_field(item['person'], 40)}: {task}"
            section = "spiky_actions.section_waiting"
        else:
            text = task
            section = "spiky_actions.section_promises"
        row = f"- [ ] {text} | [[{link}]] | {date_str}\n"
        # The heading may be in the current language or in English (existing ledgers).
        headings = t_list(section)
        for idx, l in enumerate(lines):
            if l.strip() in headings:
                end = idx + 1
                while end < len(lines) and not lines[end].startswith("## "):
                    end += 1
                while end > idx + 1 and lines[end - 1].strip() == "":
                    end -= 1  # insert above the blank line that ends the section
                lines.insert(end, row)
                break
    return "".join(lines)


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

    tasks_md = read_file(TASKS_FILE) or HEADER
    for f in candidates[:MAX_PER_RUN]:
        note = read_file(os.path.join(SPIKY_DIR, f)) or ""
        ledger = open_task_titles(tasks_md)
        items = extract(f, note, ledger)
        kept = []
        for item in items:
            if is_duplicate(item.get("task", ""), ledger + [i["task"] for i in kept]):
                log(f"{f}: duplicate dropped: {str(item.get('task'))[:60]}")
                continue
            kept.append(item)
        items = kept
        if items:
            tasks_md = append_tasks(tasks_md, items, f, note_date(f) or "")
            log(f"{f}: {len(items)} item(s)")
        else:
            log(f"{f}: no items")
        done.add(f)

    os.makedirs(os.path.dirname(TASKS_FILE), exist_ok=True)
    with open(TASKS_FILE, "w") as fh:
        fh.write(tasks_md)
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as fh:
        fh.write("\n".join(sorted(done)))
    if len(candidates) > MAX_PER_RUN:
        log(f"{len(candidates) - MAX_PER_RUN} file(s) left for the next run")


if __name__ == "__main__":
    main()
