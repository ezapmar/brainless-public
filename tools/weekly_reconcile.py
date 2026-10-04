#!/usr/bin/env python3
"""Weekly memory reconciliation for the brainless vault.

Compares _Agent-Context/CONTEXT.md against the last 7 days of reality
(briefings, close-outs, commit subjects) and writes a drift report to
_Agent-Context/CONTEXT-DRIFT.md. Report-only by design: CONTEXT.md is
never modified; the owner applies (or rejects) the proposed updates.

Safety: the LLM gets embedded context only (no file tools); the single
write is the report file. Runs Sundays 20:00 via launchd
(<prefix>.brainless.reconcile), before the 21:30 backup commit.
"""
import glob
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import vault_root  # noqa: E402
VAULT = vault_root()
sys.path.insert(0, os.path.join(VAULT, "tools"))
from llm import run_prompt
from owner_profile import OWNER, output_lang_directive  # noqa: E402
from i18n import t, t_list  # noqa: E402
from run_log import emit  # noqa: E402

CONTEXT_FILE = os.path.join(VAULT, "_Agent-Context", "CONTEXT.md")
REPORT_FILE = os.path.join(VAULT, "_Agent-Context", "CONTEXT-DRIFT.md")
STATUS_FILE = os.path.join(VAULT, ".agents", "state", "llm_status")
BRIEFING_DIR = os.path.join(VAULT, "Daily Briefings")
PER_FILE_CAP = 4000
TOTAL_CAP = 24000


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def last_week_briefings():
    chunks = []
    today = datetime.now().date()
    for i in range(7):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        for path in glob.glob(os.path.join(BRIEFING_DIR, f"daily-briefing-{d}.md")):
            try:
                with open(path, errors="replace") as fh:
                    chunks.append(f"--- {os.path.basename(path)} ---\n{fh.read()[:PER_FILE_CAP]}")
            except OSError:
                pass
    return "\n".join(chunks)


def week_commits():
    try:
        r = subprocess.run(
            ["git", "log", "--since=7.days", "--format=%ad %s", "--date=short"],
            cwd=VAULT, capture_output=True, text=True, timeout=30,
        )
        return r.stdout.strip()[:3000]
    except Exception:
        return ""


def llm_failure():
    """The breadcrumb llm.py leaves: 'timeout', 'auth', 'error' plus detail."""
    try:
        with open(STATUS_FILE, errors="replace") as fh:
            parts = fh.read().strip().split("\t")
        return " ".join(parts[1:3]) or "unknown"
    except OSError:
        return "unknown"


def main():
    """Exit 1 when the report could not be written. On 27/09/2026 the model
    call ran into its timeout, this returned 0, and the run log showed 'ok'
    over a report that was a week old."""
    try:
        with open(CONTEXT_FILE, errors="replace") as fh:
            context_md = fh.read()
    except OSError as e:
        log(f"CONTEXT.md unreadable: {e}")
        emit(report=0)
        return 1

    briefings = last_week_briefings()
    commits = week_commits()
    if not briefings and not commits:
        log("No data in the last 7 days, reconciliation skipped.")
        emit(report=0)
        return 0

    evidence = ""
    if briefings:
        evidence += f"\n# BRIEFINGS AND CLOSE-OUTS OF THE LAST 7 DAYS:\n{briefings}"
    if commits:
        evidence += f"\n# COMMIT SUBJECTS OF THE LAST 7 DAYS:\n{commits}"
    evidence = evidence[:TOTAL_CAP]

    date_str = datetime.now().strftime("%Y-%m-%d")
    prompt = f"""You are the weekly memory reconciliation assistant of the 'brainless' system for {OWNER}. {output_lang_directive()}
Task: compare CONTEXT.md (the agents' entry point) with the reality of the last 7 days and write a drift report.

RULES:
- Rely on the evidence only; claim nothing that is not in the evidence.
- Keep the report short (at most 25 lines). If there is no drift, write a single line: "{t("weekly_reconcile.no_drift")}"
- You do not change CONTEXT; you only propose.

REPORT FORMAT (markdown, use exactly these headings):
{t("weekly_reconcile.heading_stale")}
(items in CONTEXT that contradict reality; 1 line per item + the evidence source)
{t("weekly_reconcile.heading_missing")}
(things from the last 7 days that should enter CONTEXT)
{t("weekly_reconcile.heading_proposed")}
(short draft items ready to be written into CONTEXT)

# CURRENT CONTEXT.MD:
{context_md}
{evidence}"""

    result = run_prompt(prompt, timeout=300, lane="reconcile")
    if not result:
        log(f"LLM call failed ({llm_failure()}); report not written.")
        emit(report=0)
        return 1

    header = (
        t("weekly_reconcile.report_title") + "\n\n"
        + t("weekly_reconcile.report_intro", date=date_str, owner=OWNER) + "\n\n"
    )
    with open(REPORT_FILE, "w") as fh:
        fh.write(header + result + "\n")
    log(f"Report written: {REPORT_FILE}")
    emit(report=1)

    no_drift = any(m in result for m in t_list("weekly_reconcile.no_drift_marker"))
    if not no_drift and shutil.which("osascript"):  # macOS-only notification
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{t("weekly_reconcile.notify_text")}" with title "brainless"'],
            check=False,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
