#!/usr/bin/env python3
"""Evening close-out for the brainless vault.

Appends an evening close-out section (locale key evening_closeout.section_marker)
to today's briefing file. Two jobs:
  1. Reflect: what happened today, what carries over, open loops.
  2. Compound (forcing function): turn today's raw material into durable
     thinking by PROPOSING one candidate seed, one decision worth
     crystallising, and any belief/action contradiction. It also lists
     overdue calibration reviews (computed, never guessed).

The section only ever PROPOSES. It never writes to Thinking/; the vault is
human-authored truth (AGENT-RULES rule 2). The owner commits the seed/decision
by hand; the briefing just makes that a paste-away, not a blank page.

Safety by design:
- Read-only against the vault; the ONLY write is appending one section to
  today's briefing file (created if the morning run didn't happen).
- The LLM gets its context embedded in the prompt and is never granted
  file tools, so it cannot write, move, or delete anything.
- Skips the LLM call entirely on idle days (no captures, no file changes).
- Overdue calibration reviews are computed in Python (calibrate.scan), so
  that block is deterministic and cannot be hallucinated.

Run `--dry-run` to print the section to stdout instead of writing it.

Runs daily at 21:00 via the scheduler (<prefix>.brainless.closeout), before
the 21:30 backup commit and the 23:00 nightly archiver.
"""
import os
import subprocess
import sys
from datetime import datetime

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
sys.path.insert(0, os.path.join(VAULT, "tools"))
from llm import run_prompt
from calibrate import scan as calibration_scan
from owner_profile import OWNER, output_lang_directive  # noqa: E402
from i18n import t, t_list  # noqa: E402

CAPTURE_DIR = os.path.join(VAULT, "Thinking", "Daily")
TASKS_FILE = os.path.join(VAULT, "_Agent-Context", "TASKS.md")  # the single task ledger
BELIEFS_FILE = os.path.join(VAULT, "_Agent-Context", "BELIEFS-SUMMARY.md")
BRIEFING_DIR = os.path.join(VAULT, "Daily Briefings")
STATUS_FILE = os.path.join(VAULT, ".agents", "state", "llm_status")
MAX_CONTEXT = 15000  # chars of embedded context; keeps the call cheap
MARKER = t("evening_closeout.section_marker")
# Older briefings may carry the marker in another language; detect all of them.
MARKERS = t_list("evening_closeout.section_marker")


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def latest_llm_status():
    """Read the breadcrumb llm.py leaves, so a failed run names its real cause
    (auth expiry vs timeout) instead of a vague 'nothing to close'."""
    try:
        with open(STATUS_FILE, errors="replace") as fh:
            parts = fh.read().strip().split("\t")
        return (parts[1] if len(parts) > 1 else "error",
                parts[2] if len(parts) > 2 else "")
    except OSError:
        return (None, "")


def todays_captures():
    chunks = []
    if os.path.isdir(CAPTURE_DIR):
        for name in sorted(os.listdir(CAPTURE_DIR)):
            path = os.path.join(CAPTURE_DIR, name)
            # Only .md: photo captures leave .jpg files in Thinking/Daily;
            # binary content in the prompt makes subprocess choke on a null byte.
            if os.path.isfile(path) and not name.startswith(".") and name.endswith(".md"):
                try:
                    with open(path, errors="replace") as fh:
                        chunks.append(f"--- {name} ---\n{fh.read()}")
                except OSError:
                    pass
    return "\n".join(chunks)


def todays_changes():
    """Names of vault files touched today, from git (status + today's commits)."""
    names = set()
    try:
        r = subprocess.run(
            ["git", "log", "--since=midnight", "--name-only", "--format="],
            cwd=VAULT, capture_output=True, text=True, timeout=30,
        )
        names.update(l for l in r.stdout.splitlines() if l.strip())
        r = subprocess.run(
            ["git", "status", "--porcelain"], cwd=VAULT,
            capture_output=True, text=True, timeout=30,
        )
        names.update(l[3:] for l in r.stdout.splitlines() if l.strip())
    except Exception:
        pass
    # Plumbing noise (logs, agent state) is not "what happened today".
    interesting = [n for n in sorted(names)
                   if not n.startswith(("logs/", ".agents/", ".wiki/_lint", "_Agent-Context/HEALTH"))]
    return "\n".join(interesting[:80])


def open_tasks():
    try:
        with open(TASKS_FILE, errors="replace") as fh:
            return "\n".join(l for l in fh.read().splitlines()
                             if l.strip().startswith("- [ ]"))[:3000]
    except OSError:
        return ""


def beliefs_summary():
    """The Core Beliefs one-liners: reference for seed + contradiction."""
    try:
        with open(BELIEFS_FILE, errors="replace") as fh:
            text = fh.read()
    except OSError:
        return ""
    out, grab = [], False
    for line in text.splitlines():
        if line.startswith("## Core Beliefs"):
            grab = True
            continue
        if grab and line.startswith("## "):
            break
        if grab and line.strip():
            out.append(line.strip())
    return "\n".join(out)


def calibration_block():
    """Deterministic list of decisions needing attention (never LLM-guessed)."""
    due, needs_pred, no_review = calibration_scan()
    if not (due or needs_pred or no_review):
        return ""
    lines = [t("evening_closeout.calendar_heading")]
    if due:
        lines.append(t("evening_closeout.due_heading"))
        for name, rev in due:
            lines.append(f"- [[{name}]] (review {rev})")
    if needs_pred:
        lines.append(t("evening_closeout.needs_pred_heading"))
        for name in needs_pred:
            lines.append(f"- [[{name}]]")
    if no_review:
        lines.append(t("evening_closeout.no_review_heading"))
        for name in no_review:
            lines.append(f"- [[{name}]]")
    return "\n".join(lines)


def build_section(date_str, captures, changes):
    """Assemble the close-out section. Caller guarantees material exists, so a
    None return here means the LLM call itself failed (see latest_llm_status)."""
    context = ""
    if captures:
        context += f"\n# TODAY'S RAW NOTES (Thinking/Daily):\n{captures}"
    if changes:
        context += f"\n# FILES TOUCHED TODAY:\n{changes}"
    tasks = open_tasks()
    if tasks:
        context += f"\n# OPEN TASKS (_Agent-Context/TASKS.md):\n{tasks}"
    context = context[:MAX_CONTEXT]

    beliefs = beliefs_summary()
    none = t("evening_closeout.none_word")

    prompt = f"""You are the evening close-out assistant of the 'brainless' system for {OWNER}.
Date: {date_str}. Based on the context below, write a SHORT daily close-out. {output_lang_directive()} Use exactly these headings:

{t("evening_closeout.heading_what_happened")}
(at most 3 bullets; do not repeat the file list, extract the meaning)
{t("evening_closeout.heading_carry_over")}
(concrete, at most 3 bullets; if there is nothing, write "{none}")
{t("evening_closeout.heading_open_loops")}
(things waiting for an answer or at risk of being forgotten; skip the section if there are none)

{t("evening_closeout.heading_thinking_loop")}
Purpose: turn the day's raw material into durable thinking. Propose ONLY what is genuinely in the context; do not force it, do not invent. If there is nothing, write "{none}" for that item. These are PROPOSALS; {OWNER} will process them by hand.
- {t("evening_closeout.seed_label")}: 1 open question arising from today's notes/work. Format: **Question?** + why it matters (1 sentence) + which existing note it connects to ([[Note name]]). Make it paste-ready for Thinking/Ideas/.
- {t("evening_closeout.decision_label")}: if a decision under Work/ is maturing today, propose moving it to Thinking/Decisions/ and add a falsifiable prediction sentence. Otherwise "{none}".
- {t("evening_closeout.contradiction_label")}: if one of the beliefs below clashes with an action/decision of today, show it in one sentence. Otherwise skip this item.

BELIEFS OF {OWNER} (reference for the seed and the contradiction):
{beliefs or "(no summary found)"}

Write only the markdown content, nothing else. Do not invent: write nothing that is not in the context. Do NOT mention decision review dates (the system appends that list).
{context}"""

    result = run_prompt(prompt, timeout=300)
    if not result:
        return None

    calib = calibration_block()
    if calib:
        result = f"{result}\n\n{calib}"
    return f"\n\n---\n\n{MARKER}\n\n{result}\n"


def main():
    dry_run = "--dry-run" in sys.argv
    date_str = datetime.now().strftime("%Y-%m-%d")
    briefing_path = os.path.join(BRIEFING_DIR, f"daily-briefing-{date_str}.md")

    captures = todays_captures()
    changes = todays_changes()
    if not captures and not changes:
        log("Nothing to close: no captures and no file changes today.")
        return

    if os.path.exists(briefing_path) and not dry_run:
        with open(briefing_path, errors="replace") as fh:
            text = fh.read()
        if any(m in text for m in MARKERS):
                log("Close-out already present for today. Skipping.")
                return

    section = build_section(date_str, captures, changes)
    if section is None:
        outcome, detail = latest_llm_status()
        if outcome == "auth":
            log(f"LLM authentication expired ({detail[:80]}). "
                f"Log in again with `claude` in a terminal; captures/changes are ready.")
        elif outcome == "timeout":
            log("LLM call timed out; close-out not written.")
        else:
            log(f"LLM call failed ({outcome or 'unknown'}): {detail[:80]}")
        return

    if dry_run:
        print(section)
        return

    os.makedirs(BRIEFING_DIR, exist_ok=True)
    if not os.path.exists(briefing_path):
        header = (t("evening_closeout.missing_briefing_title", date=date_str) + "\n\n"
                  + t("evening_closeout.missing_briefing_note") + "\n")
        section = header + section
    with open(briefing_path, "a") as fh:
        fh.write(section)
    log(f"Close-out appended to {briefing_path}")
    post_to_buzz(section)


def post_to_buzz(text):
    """Best effort (Buzz layer 1): post the close-out to the daily channel with the briefing identity."""
    script = os.path.join(VAULT, ".agents", "scripts", "buzz_post.sh")
    if not os.access(script, os.X_OK):
        return
    try:
        subprocess.run([script, "briefing", "daily"], input=text, text=True,
                       capture_output=True, timeout=45, cwd=VAULT)
    except Exception as exc:
        log(f"buzz post skipped: {exc}")


if __name__ == "__main__":
    main()
