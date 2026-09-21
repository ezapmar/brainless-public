#!/usr/bin/env python3
import os
import re
import glob
import json
import subprocess
from datetime import datetime
import shutil
import sys

# Configuration
VAULT_ROOT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
CAPTURE_DIR = os.path.join(VAULT_ROOT, 'Thinking/Daily')
ARCHIVE_DIR = os.path.join(VAULT_ROOT, 'Archive/Daily-Captures')
DIGESTS_DIR = os.path.join(VAULT_ROOT, '.wiki/digests')
PROJECTS_WORK_DIR = os.path.join(VAULT_ROOT, 'Work')
PROJECTS_PERSONAL_DIR = os.path.join(VAULT_ROOT, 'Personal')
# The compile makes serial LLM calls of up to 300s each. 30 minutes cut a large
# backlog short every night (Sep 8-10, 2026); 90 minutes lets it drain.
COMPILE_TIMEOUT = int(os.environ.get("BRAINLESS_COMPILE_TIMEOUT", "5400"))
# A window alone was not enough: a backlog larger than the window meant the hard
# kill landed mid-phase every night (Sep 15, 17, 19, 21, 2026), took the cheap
# phases behind it (INDEX.md) with it, and threw away the child's buffered
# output, so the journal could not even show where it stopped. The compile now
# gets its own smaller budget and stops itself between items; the timeout stays
# as the backstop for a genuine hang, and the child runs unbuffered so its
# progress reaches the journal as it happens.
COMPILE_BUDGET = int(os.environ.get("BRAINLESS_COMPILE_BUDGET",
                                    str(max(600, COMPILE_TIMEOUT - 600))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from resolve_bin import resolve_claude
from llm import run_prompt  # noqa: E402
from owner_profile import OWNER, lang_name, CROSS_LINK_RULE  # noqa: E402
from i18n import t, t_list  # noqa: E402
from task_dedup import is_duplicate  # noqa: E402

CLAUDE_PATH = resolve_claude()

# Digest budget. Every capture used to enter the prompt whole, so one heavy day
# blew the context, and every note, however small, got the same narrative
# treatment. note_classify.py's label now sets the size: a task is one sitting
# of work and needs no story, only its action items; a story gets three lines;
# an epic is read in full, because weekly_research.py builds on it on Sunday.
PER_FILE_CAP = 4000
EPIC_FILE_CAP = 8000
TOTAL_CAP = 24000          # the same ceiling weekly_reconcile.py uses
BUDGET = {
    "task": "do NOT narrate it under Daily Notes; extract its action items only",
    "story": "at most 3 lines under Daily Notes",
    "epic": "full treatment: enrich, connect, cross-link",
}


def get_daily_notes():
    patterns = ['*.md', '*.txt', '*.log']
    files = []
    for p in patterns:
        files.extend(glob.glob(os.path.join(CAPTURE_DIR, p)))
    return files

def read_projects():
    projects = []
    for base, label in [(PROJECTS_WORK_DIR, 'work'), (PROJECTS_PERSONAL_DIR, 'personal')]:
        if os.path.exists(base):
            for name in os.listdir(base):
                proj = os.path.join(base, name)
                # Only real projects (folders with a notes.md), not area/doc homes.
                if os.path.isdir(proj) and os.path.exists(os.path.join(proj, 'notes.md')):
                    projects.append(f"[[{name}]] ({label})")
    return projects

def process_notes(files, labels=None):
    labels = labels or {}
    raw_content = ""
    for f in files:
        label = labels.get(os.path.basename(f), "story")
        cap = EPIC_FILE_CAP if label == "epic" else PER_FILE_CAP
        try:
            with open(f, 'r') as file:
                raw_content += f"\n--- Source: {os.path.basename(f)} [{label}] ---\n"
                raw_content += file.read()[:cap]
                raw_content += "\n"
        except Exception as e:
            print(f"Error reading {f}: {e}")
    raw_content = raw_content[:TOTAL_CAP]

    projects = read_projects()
    date_str = datetime.now().strftime("%Y-%m-%d")

    prompt = f"""
You are the processor for the 'brainless' Second Brain system.
Today's Date: {date_str}

RAW NOTES CAPTURED TODAY:
{raw_content}

TASKS:
1. Enrich the notes: Clean up typos, expand shorthand, and organize logically.
2. Research connections: Link to existing projects if mentioned. Known projects: {', '.join(projects)}.
3. Output a structured daily digest in English.
4. Cross-link personal↔work effects explicitly. {CROSS_LINK_RULE}

WEIGHT BUDGET. Each source header carries a tag set by a classifier, not by you.
The budget is not optional; a digest that narrates every errand is the failure
this system exists to avoid.
- [task]: {BUDGET['task']}.
- [story]: {BUDGET['story']}.
- [epic]: {BUDGET['epic']}.

OUTPUT STRUCTURE:
---
lang: en
compiled_at: {date_str}
source: Thinking/Daily/
---
# {date_str} - Daily Digest
## Executive Summary (3 sentences)
## Daily Notes (cleaned and enriched)
## Connections (linked projects and ideas, link to .wiki/articles/ and .wiki/projects/)
## Personal↔Work Cross-effects (if any)
## Action Items (extractions for TASKS.md)
(Only {OWNER}'s OWN next actions, one per line as "- [ ] ...", imperative, max 12 words each, {lang_name()}. No tags, no wikilinks, no tasks that belong to other people. Skip vague items.)

Output ONLY the markdown content. No preamble.
"""

    # Phase 0 T3: through tools/llm.py (tool-deny list, llm_status breadcrumb,
    # provider switch). The digest prompt is text only, so no tools are needed.
    try:
        result = run_prompt(prompt, timeout=600, lane="nightly")
    except Exception as e:
        print(f"Exception: {e}")
        return None
    if not result:
        print("Error calling the LLM (see .agents/state/llm_status)")
        return None
    return result.strip()


TASKS_FILE = os.path.join(VAULT_ROOT, '_Agent-Context/TASKS.md')
TASKS_SECTION = t("nightly_processor.tasks_section")
# Existing ledgers may carry the heading in another language; accept all of them.
TASKS_SECTIONS = t_list("nightly_processor.tasks_section")


def _clean_task(text, limit=160):
    """Single-line, pipe-free, tag-free title so gtasks_sync/task_reminder parse it
    and Google Tasks gets a readable title. Mirrors spiky_actions._clean_field."""
    text = re.sub(r"\s#[\w/-]+", "", text)             # drop #tags
    text = re.sub(r"\(from \d{4}-\d{2}-\d{2}\)", "", text)
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = text.replace("|", "/").replace("[[", "(").replace("]]", ")")
    return re.sub(r"\s{2,}", " ", text).strip().rstrip(".")[:limit]


def sync_tasks(summary, date_str):
    """Append the digest's action items to _Agent-Context/TASKS.md under the
    promises section in the ledger format `- [ ] text | [[source]] | date`, so
    gtasks_sync (Google Tasks) and task_reminder (Telegram) see them."""
    if "## Action Items" not in summary:
        return
    task_section = summary.split("## Action Items", 1)[1]
    raw = [re.sub(r"^- \[ \]\s*", "", l.strip()) for l in task_section.splitlines()
           if l.strip().startswith("- [ ]")]
    titles = [t for t in (_clean_task(r) for r in raw) if t]
    if not titles:
        return
    try:
        with open(TASKS_FILE) as f:
            content = f.read()
    except OSError:
        content = t("nightly_processor.ledger_template")
    existing_titles = [m.group(1).split(" | ")[0].strip()
                       for m in re.finditer(r"- \[[ x]\] (.+)", content)]
    existing = {x.casefold() for x in existing_titles}
    # Exact match first (cheap), then the same near-duplicate guard that
    # spiky_actions.py runs, so a promise rephrased by tonight's model does
    # not become a second row. This was how the ledger reached 130 open rows.
    rows, accepted, dropped = [], [], []
    for title in titles:
        if title.casefold() in existing or is_duplicate(title, existing_titles + accepted):
            dropped.append(title)
            continue
        accepted.append(title)
        rows.append(f"- [ ] {title} | [[{date_str}]] | {date_str}\n")
    if dropped:
        print(f"Tasks: {len(dropped)} near-duplicate(s) dropped: "
              + "; ".join(d[:50] for d in dropped[:5]))
    if not rows:
        print("Tasks: nothing new (all already in ledger)")
        return
    lines = content.splitlines(keepends=True)
    stripped = [l.strip() for l in lines]
    section = next((h for h in TASKS_SECTIONS if h in stripped), None)
    if section is None:
        section = TASKS_SECTION
        lines.append(f"\n{section}\n")
    for idx, l in enumerate(lines):
        if l.strip() == section:
            end = idx + 1
            while end < len(lines) and not lines[end].startswith("## "):
                end += 1
            while end > idx + 1 and lines[end - 1].strip() == "":
                end -= 1
            lines[end:end] = rows
            break
    with open(TASKS_FILE, 'w') as f:
        f.write("".join(lines))
    print(f"Synced {len(rows)} tasks to _Agent-Context/TASKS.md")

NOTE_TAGS = os.path.join(VAULT_ROOT, '.agents/state/note_tags.jsonl')


def weight_block(date_str):
    """A deterministic '#type/...' line per note classified for this date.

    The digest itself is one LLM blob, so asking the model to tag each entry
    would be unreliable and unverifiable. These tags come straight from
    note_classify.py's state file instead, which is also what weekly_research.py
    reads, so what the digest shows and what triggers research cannot drift.
    Returns "" when nothing was classified, so the digest is unchanged.
    """
    latest = {}
    try:
        with open(NOTE_TAGS, errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return ""
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("date") == date_str:
            latest[rec.get("path")] = rec  # append-only file: the last line wins
    if not latest:
        return ""
    out = ["", "## " + t("nightly_processor.weight_heading"), ""]
    for path, rec in sorted(latest.items()):
        out.append(f"- `{path}` #type/{rec.get('label', 'task')} "
                   f"(score {rec.get('score')}, {rec.get('method')})")
    if any(r.get("label") == "epic" for r in latest.values()):
        out.append("")
        out.append(t("nightly_processor.weight_epic_note"))
    return "\n".join(out) + "\n"


def classify_today(notes):
    """Label the day's captures before the digest reads them.

    note_classify.py runs on its own timer at 06:40 and tags a note with the
    note's own date, so at 23:00 tonight's captures are still unlabelled and
    the weight block could only report them the next morning. Running it here
    first means the labels exist when the prompt is built. Stage A is
    deterministic and instant; stage B calls a model only for epic candidates,
    and the morning run then finds these paths in state and skips them.
    """
    try:
        subprocess.run(
            [sys.executable, os.path.join(VAULT_ROOT, 'tools/note_classify.py'), '--days', '1'],
            cwd=VAULT_ROOT, check=False, timeout=900,
        )
    except Exception as e:
        print(f"note_classify skipped: {e}")
    wanted = {os.path.relpath(f, VAULT_ROOT): os.path.basename(f) for f in notes}
    labels = {}
    try:
        with open(NOTE_TAGS, errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                name = wanted.get(rec.get("path"))
                if name:
                    labels[name] = rec.get("label", "task")  # append-only: last line wins
    except OSError:
        pass
    return labels


def budget_line(notes, labels):
    counts = {"task": 0, "story": 0, "epic": 0}
    for f in notes:
        counts[labels.get(os.path.basename(f), "story")] += 1
    return t("nightly_processor.budget_line", tasks=counts["task"], stories=counts["story"],
             epics=counts["epic"], per_file=PER_FILE_CAP, total=TOTAL_CAP)


def main():
    if not os.path.exists(CAPTURE_DIR):
        os.makedirs(CAPTURE_DIR)

    notes = get_daily_notes()
    if not notes:
        # Heartbeat line: health_check.py watches this log's mtime to know
        # the job is alive even on days with nothing to process.
        print(f"{datetime.now()}: No capture files.")
        return

    print(f"{datetime.now()}: Processing {len(notes)} files...")
    labels = classify_today(notes)
    summary = process_notes(notes, labels)

    if summary:
        date_str = datetime.now().strftime("%Y-%m-%d")
        summary = summary.rstrip() + "\n" + weight_block(date_str) + "\n" + budget_line(notes, labels) + "\n"
        date_filename = date_str + ".md"
        os.makedirs(DIGESTS_DIR, exist_ok=True)
        output_path = os.path.join(DIGESTS_DIR, date_filename)
        
        try:
            with open(output_path, 'w') as f:
                f.write(summary)
            print(f"Summary saved to {output_path}")
            
            # Sync tasks to central file
            sync_tasks(summary, date_str)
            
            date_archive_dir = os.path.join(ARCHIVE_DIR, datetime.now().strftime("%Y-%m-%d"))
            os.makedirs(date_archive_dir, exist_ok=True)
            for f in notes:
                shutil.move(f, date_archive_dir)
            print(f"Archived {len(notes)} files.")

            # After daily digest, run incremental wiki compile
            try:
                subprocess.run(
                    [sys.executable, "-u",
                     os.path.join(VAULT_ROOT, 'tools/compile_resources.py'),
                     f"--budget-seconds={COMPILE_BUDGET}"],
                    cwd=VAULT_ROOT, check=False, timeout=COMPILE_TIMEOUT,
                )
            except Exception as e:
                print(f"compile_resources error: {e}")

            # Always refresh the lint report (non-blocking, report-only mode)
            try:
                subprocess.run(
                    [sys.executable, os.path.join(VAULT_ROOT, 'tools/lint_wiki.py')],
                    cwd=VAULT_ROOT, check=False, timeout=180,
                )
            except Exception as e:
                print(f"lint_wiki report refresh skipped: {e}")
        except Exception as e:
            print(f"Error: {e}")
    else:
        print("Failed to generate summary.")
        if shutil.which("osascript"):  # macOS-only notification
            import subprocess as _sp
            _sp.run(["osascript", "-e",
                     'display notification "Nightly processor: Claude returned no summary" with title "brainless"'],
                    check=False)

if __name__ == "__main__":
    main()
