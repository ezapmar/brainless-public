#!/usr/bin/env python3
"""Weekly note resurfacing for the brainless vault.

Picks 5 old notes worth re-reading this week, chosen for relevance to the
active projects in CONTEXT.md, and writes them to _Agent-Context/RESURFACE.md.
Briefing sessions include that list while it is fresh (under 7 days old).
Counters the collector's fallacy: captured notes nobody ever sees again.

Safety: the LLM only sees note titles and snippets embedded in the prompt;
the single write is RESURFACE.md. Runs Mondays 06:30 via launchd
(<prefix>.brainless.resurface).
"""
import os
import sys
import time
from datetime import datetime

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
sys.path.insert(0, os.path.join(VAULT, "tools"))
from llm import run_prompt
from owner_profile import OWNER, output_lang_directive  # noqa: E402
from i18n import t  # noqa: E402

CONTEXT_FILE = os.path.join(VAULT, "_Agent-Context", "CONTEXT.md")
OUT_FILE = os.path.join(VAULT, "_Agent-Context", "RESURFACE.md")
SOURCE_DIRS = ["Thinking", "Work", "Personal", "Library"]
EXCLUDE_PARTS = {"archive", "_backup", "_attachments", ".wiki", ".git", ".agents",
                 "venv", "__pycache__", "daily briefings", "resources"}
MIN_AGE_DAYS = 30       # only notes untouched for at least this long
MAX_CANDIDATES = 120    # cap the list shown to the model
SNIPPET_CHARS = 160


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def collect_candidates():
    cutoff = time.time() - MIN_AGE_DAYS * 86400
    found = []
    for base in SOURCE_DIRS:
        root_dir = os.path.join(VAULT, base)
        if not os.path.isdir(root_dir):
            continue
        for root, dirs, files in os.walk(root_dir):
            dirs[:] = [d for d in dirs if not d.startswith(".")
                       and d.lower() not in EXCLUDE_PARTS]
            for name in files:
                if not name.endswith(".md") or name.endswith("_raw.md"):
                    continue
                path = os.path.join(root, name)
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    continue
                if mtime > cutoff:
                    continue
                rel = os.path.relpath(path, VAULT)
                try:
                    with open(path, errors="replace") as fh:
                        snippet = fh.read(2000)
                except OSError:
                    continue
                title = next((l.lstrip("# ").strip() for l in snippet.splitlines()
                              if l.startswith("#")), os.path.splitext(name)[0])
                body = " ".join(snippet.split())[:SNIPPET_CHARS]
                found.append((mtime, rel, title, body))
    # Oldest first, so long-forgotten notes get a chance before merely stale ones.
    found.sort(key=lambda t: t[0])
    return found[:MAX_CANDIDATES]


def main():
    try:
        with open(CONTEXT_FILE, errors="replace") as fh:
            context_md = fh.read()[:6000]
    except OSError as e:
        log(f"CONTEXT.md unreadable: {e}")
        return

    candidates = collect_candidates()
    if len(candidates) < 5:
        log(f"Only {len(candidates)} candidates, resurface skipped.")
        return

    lines = []
    for mtime, rel, title, body in candidates:
        age_days = int((time.time() - mtime) / 86400)
        lines.append(f"- {rel} | {title} | untouched for {age_days} days | {body}")
    catalog = "\n".join(lines)

    date_str = datetime.now().strftime("%Y-%m-%d")
    prompt = f"""You are the note resurfacing assistant of the 'brainless' system for {OWNER}. {output_lang_directive()}
Below is a list of notes untouched for 30+ days and the current context of the owner, {OWNER}.
Pick the 5 notes MOST worth re-reading this week.

SELECTION CRITERION: notes that can contribute to the active projects and priorities today.
Usefulness, not nostalgia. Never invent a note that is not in the list.

OUTPUT FORMAT (write only this):
- [[<file path, without the .md extension>]]: <one sentence, why it should be read this week>
(exactly 5 items)

# CURRENT CONTEXT (CONTEXT.md):
{context_md}

# CANDIDATE NOTES:
{catalog}"""

    result = run_prompt(prompt, timeout=300)
    if not result:
        log("LLM call failed; RESURFACE.md not written.")
        return

    header = (
        t("resurface.title") + "\n\n"
        + t("resurface.intro", date=date_str) + "\n\n"
    )
    with open(OUT_FILE, "w") as fh:
        fh.write(header + result + "\n")
    log(f"Written: {OUT_FILE} (out of {len(candidates)} candidates)")


if __name__ == "__main__":
    main()
