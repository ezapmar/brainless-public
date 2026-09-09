#!/usr/bin/env python3
import os
import re
import glob
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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from resolve_bin import resolve_claude
from llm import run_prompt  # noqa: E402
from owner_profile import OWNER, lang_name, CROSS_LINK_RULE  # noqa: E402

CLAUDE_PATH = resolve_claude()


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

def process_notes(files):
    raw_content = ""
    for f in files:
        try:
            with open(f, 'r') as file:
                raw_content += f"\n--- Source: {os.path.basename(f)} ---\n"
                raw_content += file.read()
                raw_content += "\n"
        except Exception as e:
            print(f"Error reading {f}: {e}")
    
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

OUTPUT STRUCTURE:
---
lang: en
compiled_at: {date_str}
source: Thinking/Daily/
---
# {date_str} — Daily Digest
## Executive Summary (3 sentences)
## Daily Notes (cleaned and enriched)
## Connections (linked projects and ideas — link to .wiki/articles/ and .wiki/projects/)
## Personal↔Work Cross-effects (if any)
## Action Items (extractions for TASKS.md)
(Only {OWNER}'s OWN next actions, one per line as "- [ ] ...", imperative, max 12 words each, {lang_name()}. No tags, no wikilinks, no tasks that belong to other people. Skip vague items.)

Output ONLY the markdown content. No preamble.
"""

    # Phase 0 T3: through tools/llm.py (tool-deny list, llm_status breadcrumb,
    # provider switch). The digest prompt is text only, so no tools are needed.
    try:
        result = run_prompt(prompt, timeout=600)
    except Exception as e:
        print(f"Exception: {e}")
        return None
    if not result:
        print("Error calling the LLM (see .agents/state/llm_status)")
        return None
    return result.strip()


TASKS_FILE = os.path.join(VAULT_ROOT, '_Agent-Context/TASKS.md')
TASKS_SECTION = "## Sözlerim"


def _clean_task(text, limit=160):
    """Single-line, pipe-free, tag-free title so gtasks_sync/task_reminder parse it
    and Google Tasks gets a readable title. Mirrors spiky_actions._clean_field."""
    text = re.sub(r"\s#[\w/-]+", "", text)             # drop #tags
    text = re.sub(r"\(from \d{4}-\d{2}-\d{2}\)", "", text)
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = text.replace("|", "/").replace("[[", "(").replace("]]", ")")
    return re.sub(r"\s{2,}", " ", text).strip().rstrip(".")[:limit]


def sync_tasks(summary, date_str):
    """Append the digest's action items to _Agent-Context/TASKS.md under
    '## Sözlerim' in the ledger format `- [ ] text | [[source]] | date`, so
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
        content = "# Görev Defteri\n\n## Sözlerim\n\n## Bekliyorum\n"
    existing = {m.group(1).split(" | ")[0].strip().casefold()
                for m in re.finditer(r"- \[[ x]\] (.+)", content)}
    rows = [f"- [ ] {t} | [[{date_str}]] | {date_str}\n"
            for t in titles if t.casefold() not in existing]
    if not rows:
        print("Tasks: nothing new (all already in ledger)")
        return
    lines = content.splitlines(keepends=True)
    if TASKS_SECTION not in [l.strip() for l in lines]:
        lines.append(f"\n{TASKS_SECTION}\n")
    for idx, l in enumerate(lines):
        if l.strip() == TASKS_SECTION:
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
    summary = process_notes(notes)
    
    if summary:
        date_str = datetime.now().strftime("%Y-%m-%d")
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
                    [sys.executable, os.path.join(VAULT_ROOT, 'tools/compile_resources.py')],
                    cwd=VAULT_ROOT, check=False, timeout=1800,
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
