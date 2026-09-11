#!/usr/bin/env python3
"""Pipeline heartbeat for the brainless vault.

Checks the plumbing (backups, processors, logs) and writes a compact status
block to _Agent-Context/HEALTH.md. Briefings must surface this block.
Fires a macOS notification when any check crosses the 2-day red-flag line.
Runs hourly from cron_wrapper.sh; cheap by design (no LLM calls).
"""
import os
import subprocess
import time
from datetime import datetime

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from owner_profile import WORKER  # noqa: E402
from i18n import t  # noqa: E402
HEALTH_FILE = os.path.join(VAULT, "_Agent-Context", "HEALTH.md")
RED_FLAG_SECONDS = 2 * 24 * 3600

CHECKS = []  # (label, status, detail) with status in {OK, WARN, RED}


def add(label, status, detail):
    CHECKS.append((label, status, detail))


def age_str(seconds):
    if seconds < 3600:
        return f"{int(seconds // 60)} {t('health_check.unit_min')}"
    if seconds < 86400:
        return f"{seconds / 3600:.1f} {t('health_check.unit_hour')}"
    return f"{seconds / 86400:.1f} {t('health_check.unit_day')}"


def ago(seconds):
    """'<age> ago' in the output language."""
    return t("health_check.ago", age=age_str(seconds))


def check_git():
    now = time.time()
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%ct"], cwd=VAULT,
            capture_output=True, text=True, timeout=30,
        )
        last_commit = int(out.stdout.strip())
        age = now - last_commit
        status = "RED" if age > RED_FLAG_SECONDS else ("WARN" if age > 86400 else "OK")
        add(t("health_check.last_commit"), status, ago(age))
    except Exception as e:
        add(t("health_check.last_commit"), "RED", t("health_check.unreadable", error=e))

    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"], cwd=VAULT,
            capture_output=True, text=True, timeout=30,
        )
        dirty = len([l for l in out.stdout.splitlines() if l.strip()])
        add(t("health_check.uncommitted_changes"), "OK" if dirty < 20 else "WARN", t("health_check.files", n=dirty))
    except Exception:
        pass

    try:
        out = subprocess.run(
            ["git", "rev-list", "--count", "origin/master..master"], cwd=VAULT,
            capture_output=True, text=True, timeout=30,
        )
        ahead = int(out.stdout.strip())
        paused = os.path.exists(os.path.join(VAULT, ".agents", "state", "no_push"))
        if paused:
            add(t("health_check.unpushed_commits"), "WARN" if ahead else "OK",
                t("health_check.commits_paused", n=ahead))
        else:
            add(t("health_check.unpushed_commits"), "OK" if ahead == 0 else ("WARN" if ahead < 10 else "RED"),
                t("health_check.commits", n=ahead))
    except Exception:
        pass


def check_log(label, path, red_after, hint):
    """Freshness of a log file: its mtime is the job's last sign of life."""
    full = os.path.join(VAULT, path)
    if not os.path.exists(full):
        add(label, "RED", t("health_check.log_missing"))
        return
    age = time.time() - os.path.getmtime(full)
    status = "RED" if age > red_after else "OK"
    add(label, status, t("health_check.log_last_trace", ago=ago(age), hint=hint))


def check_llm_auth():
    """LLM backend auth canary.

    Reads the breadcrumb llm.py drops on every real call. Freshness checks see
    a script that ran and logged; they cannot see that its LLM call 401'd. This
    catches a silent token/API-key expiry that would otherwise show green.
    """
    label = t("health_check.llm_access")
    path = os.path.join(VAULT, ".agents", "state", "llm_status")
    if not os.path.exists(path):
        add(label, "WARN", t("health_check.llm_no_record"))
        return
    try:
        with open(path, errors="replace") as fh:
            parts = fh.read().strip().split("\t")
        outcome = parts[1] if len(parts) > 1 else "error"
        detail = parts[2] if len(parts) > 2 else ""
    except Exception:
        add(label, "WARN", t("health_check.status_file_unreadable"))
        return
    age = ago(time.time() - os.path.getmtime(path))
    if outcome == "ok":
        add(label, "OK", t("health_check.llm_ok", ago=age))
    elif outcome == "auth":
        add(label, "RED", t("health_check.llm_auth", ago=age, detail=detail[:70]))
    elif outcome == "timeout":
        add(label, "WARN", t("health_check.llm_timeout", ago=age))
    else:
        add(label, "WARN", t("health_check.llm_error", ago=age, detail=detail[:70]))


def check_crm():
    """CRM snapshot canary (.agents/scripts/crm_capture.py).

    Silent when no CRM token is configured, so a clean install never shows red.
    Reads the breadcrumb the script drops on every run; log mtime alone would
    stay green while the API rejects the token every hour.
    """
    conf = os.path.expanduser("~/.config/brainless")
    try:
        with open(os.path.join(conf, "crm_provider")) as fh:
            provider = fh.read().strip().lower() or "pipedrive"
    except OSError:
        provider = "pipedrive"
    if not os.path.exists(os.path.join(conf, f"{provider}_api_token")):
        return
    label = t("health_check.crm_snapshot")
    path = os.path.join(VAULT, ".agents", "state", "crm_status")
    if not os.path.exists(path):
        add(label, "WARN", t("health_check.crm_not_run"))
        return
    try:
        with open(path, errors="replace") as fh:
            parts = fh.read().strip().split("\t")
        outcome = parts[1] if len(parts) > 1 else "error"
        detail = parts[2] if len(parts) > 2 else ""
    except Exception:
        add(label, "WARN", t("health_check.status_file_unreadable"))
        return
    age_s = time.time() - os.path.getmtime(path)
    age = ago(age_s)
    if outcome == "ok":
        status = "RED" if age_s > 26 * 3600 else "OK"
        add(label, status, t("health_check.crm_ok", detail=detail, ago=age))
    elif outcome == "auth":
        add(label, "RED", t("health_check.crm_auth", ago=age, detail=detail[:70]))
    else:
        add(label, "RED", t("health_check.crm_error", ago=age, detail=detail[:70]))


def check_log_errors():
    """Recent error lines in the processor logs."""
    # nightly log lives on the worker now; the stale Mac file must not WARN.
    for label, path in [(t("health_check.processor_errors"), "logs/smart_processor.log")]:
        full = os.path.join(VAULT, path)
        if not os.path.exists(full):
            continue
        try:
            with open(full, errors="replace") as fh:
                tail = fh.readlines()[-50:]
            # Count only the errors after the LAST successful run summary
            # ("... backed off."). Resolved or transient errors (ssh outage, an
            # old code bug) then stop producing WARN once a clean run follows;
            # the errors of a last run that crashed without a summary stay flagged.
            last_run = max((i for i, l in enumerate(tail)
                            if "backed off" in l.lower()), default=-1)
            errs = [l.strip() for l in tail[last_run + 1:]
                    if any(k in l.lower() for k in ("error", "failed", "err]", "exception"))
                    and "0 failed" not in l]
            if errs:
                add(label, "WARN", t("health_check.error_lines", n=len(errs), last=errs[-1][:120]))
            else:
                add(label, "OK", t("health_check.tail_clean"))
        except Exception:
            pass


def check_worker():
    """The nightly family runs on the always-on worker (PROFILE.md worker_name).
    The evidence the primary machine can see is git: the age of the last commit
    signed "(<worker>)". Unit-level failures are caught by the worker's watchdog;
    the indicator here is the aggregate pulse. Thresholds: 30h WARN, 52h RED (2-day rule)."""
    try:
        out = subprocess.run(
            ["git", "log", "--format=%ct\t%s", "-100"],
            capture_output=True, text=True, cwd=VAULT, timeout=30).stdout
        for line in out.splitlines():
            ct, _, subject = line.partition("\t")
            if subject.rstrip().endswith(f"({WORKER})"):
                age = time.time() - int(ct)
                status = "RED" if age > 52 * 3600 else ("WARN" if age > 30 * 3600 else "OK")
                add(t("health_check.worker_label", worker=WORKER), status,
                    t("health_check.worker_last_commit", worker=WORKER, ago=ago(age)))
                return
        add(t("health_check.worker_label", worker=WORKER), "RED",
            t("health_check.worker_no_trace", worker=WORKER))
    except Exception:
        pass


def check_dialectic():
    """Critical dialectic rounds (worker, 12:30 and 21:20) write one status
    line per run into _Agent-Context/DIALECTIC-STATUS.md, idle days included.
    The freshest copy is on origin; the Mac working tree may lag a day."""
    try:
        subprocess.run(["git", "fetch", "-q", "origin"], cwd=VAULT,
                       capture_output=True, timeout=30)
        text = subprocess.run(
            ["git", "show", "origin/master:_Agent-Context/DIALECTIC-STATUS.md"],
            cwd=VAULT, capture_output=True, text=True, timeout=30).stdout
    except Exception:
        text = ""
    if not text.strip():
        try:
            with open(os.path.join(VAULT, "_Agent-Context", "DIALECTIC-STATUS.md"),
                      errors="replace") as fh:
                text = fh.read()
        except OSError:
            add(t("health_check.dialectic_round"), "WARN", t("health_check.dialectic_no_status"))
            return
    runs = [l for l in text.splitlines() if l.startswith("- 20")]
    if not runs:
        add(t("health_check.dialectic_round"), "WARN", t("health_check.dialectic_empty"))
        return
    last = runs[-1]
    try:
        day = datetime.strptime(last[2:12], "%Y-%m-%d")
        slot = last.split()[2].rstrip(":")
        hour = 12 if slot == "noon" else 21
        age = time.time() - day.replace(hour=hour, minute=30).timestamp()
    except Exception:
        add(t("health_check.dialectic_round"), "WARN", t("health_check.dialectic_unparsable", line=last[:60]))
        return
    result = last.split(":", 1)[1].strip().split(",")[0] if ":" in last else "?"
    status = "RED" if age > 36 * 3600 else ("WARN" if age > 14 * 3600 else "OK")
    if result == "error":
        status = "RED" if status == "OK" else status
    add(t("health_check.dialectic_round"), status,
        t("health_check.dialectic_last_run", date=last[2:12], slot=slot, result=result, ago=ago(max(age, 0))))


def main():
    check_git()
    # smart_processor runs hourly; 3h of silence means the cron line is dead.
    check_log(t("health_check.hourly_processor"), "logs/smart_processor.log", 3 * 3600, t("health_check.hourly_cron"))
    # Night jobs and telegram moved to the worker on 2026-08-26; they leave no
    # Mac log trace. The aggregate pulse is below, the detail is in the Linux watchdog.
    check_worker()
    check_dialectic()
    check_llm_auth()
    check_crm()
    check_log_errors()

    worst = "OK"
    for _, status, _ in CHECKS:
        if status == "RED":
            worst = "RED"
            break
        if status == "WARN":
            worst = "WARN"

    icon = {"OK": "🟢", "WARN": "🟡", "RED": "🔴"}[worst]
    lines = [
        t("health_check.title"),
        "",
        t("health_check.status_line", icon=icon, worst=worst, time=datetime.now().strftime('%Y-%m-%d %H:%M')),
        "",
    ]
    for label, status, detail in CHECKS:
        mark = {"OK": "🟢", "WARN": "🟡", "RED": "🔴"}[status]
        lines.append(f"- {mark} {label}: {detail}")
    lines.append("")
    lines.append(t("health_check.footer"))

    os.makedirs(os.path.dirname(HEALTH_FILE), exist_ok=True)
    with open(HEALTH_FILE, "w") as fh:
        fh.write("\n".join(lines) + "\n")

    if worst == "RED":
        reds = "; ".join(f"{l}: {d}" for l, s, d in CHECKS if s == "RED")[:180]
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{reds}" with title "{t("health_check.notify_title")}"'],
            check=False,
        )


if __name__ == "__main__":
    main()
