#!/usr/bin/env python3
"""Pipeline heartbeat for the brainless vault.

Checks the plumbing (backups, processors, logs) and writes a compact status
block to _Agent-Context/HEALTH.md. Briefings must surface this block.
Fires a macOS notification when any check crosses the 2-day red-flag line.
Runs hourly from cron_wrapper.sh; cheap by design (no LLM calls).
"""
import os
import re
import json
import shutil
import subprocess
import time
from datetime import datetime, timedelta

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from owner_profile import WORKER  # noqa: E402
from owner_profile import PROTECTED_HOMES as profile_protected_homes  # noqa: E402
from i18n import t  # noqa: E402
HEALTH_FILE = os.path.join(VAULT, "_Agent-Context", "HEALTH.md")
RED_FLAG_SECONDS = 2 * 24 * 3600
PILE_INBOX_RED = 10  # tools/wiki_prune.py INBOX_STALE_RED, the belief's own criterion

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


# Homes that must never reach the remote. The list lives in PROFILE.md, because
# .gitignore, this check and tools/tests/test_gitignore_guards.py all need the same
# one and a second copy drifts silently.
PROTECTED_HOMES = list(profile_protected_homes)


def check_buzz_delivery():
    path = os.path.join(VAULT, ".agents/state/buzz_interactions_status.json")
    if not os.path.exists(path):
        return  # worker-only state; absent on the primary Mac
    try:
        status = json.loads(open(path).read())
        age = time.time() - status.get("checked_at", 0)
        pending = status.get("pending_messages", 0) + status.get("pending_replies", 0)
        level = "RED" if age > 900 else ("WARN" if pending or status.get("failures") else "OK")
        add("Buzz", level, f"{pending} pending; last completed poll {age_str(age)}")
    except (OSError, ValueError, TypeError):
        add("Buzz", "RED", "Unreadable interaction status")


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


def llm_row(label, line, age_s, auth_key="health_check.llm_auth"):
    """One llm_status line ('<stamp>\t<outcome>\t<detail>') -> an added row."""
    parts = line.strip().split("\t")
    outcome = parts[1] if len(parts) > 1 else "error"
    detail = parts[2] if len(parts) > 2 else ""
    age = ago(age_s)
    if outcome == "ok":
        add(label, "OK", t("health_check.llm_ok", ago=age))
    elif outcome == "auth":
        add(label, "RED", t(auth_key, ago=age, detail=detail[:70], worker=WORKER))
    elif outcome == "timeout":
        add(label, "WARN", t("health_check.llm_timeout", ago=age))
    else:
        add(label, "WARN", t("health_check.llm_error", ago=age, detail=detail[:70]))


def check_llm_auth():
    """LLM backend auth canary, for this machine and for the worker.

    Reads the breadcrumb llm.py drops on every real call. Freshness checks see
    a script that ran and logged; they cannot see that its LLM call 401'd. This
    catches a silent token/API-key expiry that would otherwise show green.

    llm_status is per machine and the heavy LLM work runs on the worker, so
    the worker's line gets its own row from the mirror tools/worker_reach.py
    copies over ssh (.agents/state/worker_llm_status.json). No mirror, no row:
    this machine has no worker target, or is the worker itself.
    """
    label = t("health_check.llm_access")
    path = os.path.join(VAULT, ".agents", "state", "llm_status")
    if not os.path.exists(path):
        add(label, "WARN", t("health_check.llm_no_record"))
    else:
        try:
            with open(path, errors="replace") as fh:
                line = fh.read()
        except Exception:
            add(label, "WARN", t("health_check.status_file_unreadable"))
        else:
            llm_row(label, line, time.time() - os.path.getmtime(path))

    mirror = os.path.join(VAULT, ".agents", "state", "worker_llm_status.json")
    if not os.path.exists(mirror):
        return
    label = t("health_check.llm_access_worker", worker=WORKER)
    try:
        m = json.loads(open(mirror).read())
        line, mtime = m["line"], m["mtime"]
    except (OSError, ValueError, KeyError, TypeError):
        add(label, "WARN", t("health_check.status_file_unreadable"))
        return
    llm_row(label, line, time.time() - mtime, "health_check.llm_auth_worker")


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


def check_morning_briefing():
    """The morning briefing is a Claude scheduled task that only fires while the
    desktop app is open, so it fails silently. On weekdays after 08:30 the
    day's file must exist under Daily Briefings/ and carry the health block;
    a missing morning half is WARN, never RED (the evening close-out still
    opens the file). Weekends and early hours are reported as not due."""
    label = t("health_check.morning_briefing")
    now = datetime.now()
    if now.weekday() >= 5 or (now.hour, now.minute) < (8, 30):
        add(label, "OK", t("health_check.morning_not_due"))
        return
    path = os.path.join(VAULT, "Daily Briefings", f"daily-briefing-{now.strftime('%Y-%m-%d')}.md")
    if not os.path.exists(path):
        add(label, "WARN", t("health_check.morning_missing"))
        return
    try:
        with open(path, errors="replace") as fh:
            text = fh.read()
    except OSError:
        add(label, "WARN", t("health_check.morning_missing"))
        return
    block = t("health_check.title").lstrip("# ").strip()
    if block in text or "System Health" in text:
        add(label, "OK", t("health_check.morning_ok"))
    else:
        add(label, "WARN", t("health_check.morning_no_block"))


def check_kill_criteria():
    """The quit rule with a date (tools/kill_criteria.py). A breached criterion is a
    RED row: the owner wrote down when to stop and the date passed with the box
    still open. This call also refreshes _Agent-Context/KILL-CRITERIA.md, which
    the briefing copies next to the health block."""
    label = t("health_check.kill_criteria")
    try:
        import kill_criteria
        res = kill_criteria.write()
    except Exception as e:
        add(label, "WARN", t("health_check.kill_unreadable", error=str(e)[:80]))
        return
    if res["breached"]:
        first = res["breached"][0]
        add(label, "RED", t("health_check.kill_breached", n=len(res["breached"]),
                            first=f"{first['project']} ({first['date'].isoformat()}: {first['condition'][:60]})"))
    else:
        add(label, "OK", t("health_check.kill_ok", due=len(res["due"]), days=kill_criteria.DUE_SOON_DAYS,
                           missing=len(res["missing"])))


def check_pile():
    """The belief "capture everything, filter later" names its own failure:
    Inbox files older than 14 days should sit near zero. tools/wiki_prune.py
    counts them every Sunday and leaves a machine line in the scorecard; this
    row carries it into the briefing, RED when the criterion is breached, so
    the number cannot drift for a fortnight unseen the way it did in September."""
    label = t("health_check.pile_label")
    path = os.path.join(VAULT, "_Agent-Context", "PILE-SCORECARD.md")
    try:
        with open(path, errors="replace") as fh:
            m = re.search(r"<!-- pile: (\{.*?\}) -->", fh.read())
        data = json.loads(m.group(1)) if m else None
    except (OSError, ValueError):
        data = None
    if not data:
        add(label, "WARN", t("health_check.pile_missing"))
        return
    inbox = int(data.get("inbox_stale", 0))
    decisions = data.get("decisions") or 0
    qpd = f"{data.get('queries', 0) / decisions:.1f}" if decisions else f"{data.get('queries', 0)} : 0"
    status = "RED" if inbox > PILE_INBOX_RED else ("WARN" if inbox > 0 else "OK")
    add(label, status, t("health_check.pile_detail", inbox=inbox, qpd=qpd,
                         orphans=data.get("orphans", "?"), date=data.get("date", "?")))


def _pile_line():
    try:
        with open(os.path.join(VAULT, "_Agent-Context", "PILE-SCORECARD.md"), errors="replace") as fh:
            m = re.search(r"<!-- pile: (\{.*?\}) -->", fh.read())
        return json.loads(m.group(1)) if m else None
    except (OSError, ValueError):
        return None


def check_graph():
    """Graph health from the Sunday scorecard (tools/wiki_metrics.py). A rising
    orphan rate means ingestion writes pages without connecting them, the exact
    failure that turns a wiki back into a folder, and nothing else errors."""
    data = _pile_line()
    if not data or "orphan_rate" not in data:
        return  # older scorecard; the row appears after the next Sunday count
    import wiki_metrics
    worst = "OK"
    for _, status, _ in wiki_metrics.verdicts(data):
        worst = max(worst, status, key=["OK", "WARN", "RED"].index)
    add(t("health_check.graph_label"), worst, t(
        "health_check.graph_detail", orphans=f"{data['orphan_rate']:.0%}", degree=data["avg_degree"],
        main=f"{data['main_share']:.0%}", date=data.get("date", "?")))


def _runs_text(host):
    """RUNS-<host>.md: the worker's freshest copy is on origin, the Mac's is local."""
    rel = f"_Agent-Context/RUNS-{host}.md"
    if host != "mac":
        try:
            text = subprocess.run(["git", "show", f"origin/master:{rel}"], cwd=VAULT,
                                  capture_output=True, text=True, timeout=30).stdout
            if text.strip():
                return text
        except Exception:
            pass
    try:
        with open(os.path.join(VAULT, rel), errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


# Capture paths that should produce something within these many days; zeros
# for longer mean the path is broken, not that the owner was quiet.
DRY_CAPTURE = {("nightly_processor", "captures"): 3, ("spiky_capture", "reports"): 7,
               # A week of compiles that change no page is a stuck compiler.
               ("nightly_compile", "wiki_changed"): 7}


def run_findings(roll, now=None):
    """(status, text) findings from a run-log rollup (tools/run_log.py)."""
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    by_job = {}
    for e in roll:
        by_job.setdefault(e["job"], []).append(e)
    out = []
    for job, days in sorted(by_job.items()):
        days.sort(key=lambda e: e["day"])
        last = days[-1]
        if last["status"] == "fail":
            out.append(("WARN", t("health_check.runs_failed", job=job, day=last["day"], time=last["last"])))
        # Expected gap from the job's own history: several runs a day, or one run every few days.
        if len(days) >= 2:
            span = (datetime.strptime(last["day"], "%Y-%m-%d")
                    - datetime.strptime(days[0]["day"], "%Y-%m-%d")).days or 1
            per_day = sum(e["runs"] for e in days) / max(span, 1)
            gap_days = max(1.0, 1 / per_day) if per_day else span
            quiet = (now - datetime.strptime(f"{last['day']} {last['last']}", "%Y-%m-%d %H:%M")).total_seconds() / 86400
            if quiet > 2.5 * gap_days:
                out.append(("WARN", t("health_check.runs_quiet", job=job, days=f"{quiet:.1f}")))
        # Retrieval regression: hit@5 on the golden questions fell 15+ points
        # below its best in the window (tools/retrieval_eval.py).
        scores = [e["counts"]["hit5"] for e in days if isinstance(e["counts"].get("hit5"), int)]
        if scores and max(scores) - scores[-1] >= 15:
            out.append(("WARN", t("health_check.runs_retrieval", job=job, now=scores[-1], best=max(scores))))
        for (j, key), limit in DRY_CAPTURE.items():
            if j != job:
                continue
            # Only runs that report the field: one added later must not read
            # its older runs as zeros.
            keyed = [e for e in days if key in e["counts"]]
            if not keyed:
                continue
            recent = [e for e in keyed if e["day"] > (now - timedelta(days=limit)).strftime("%Y-%m-%d")]
            covered = keyed[0]["day"] <= (now - timedelta(days=limit - 1)).strftime("%Y-%m-%d")
            if covered and recent and all(e["counts"].get(key, 0) == 0 for e in recent):
                out.append(("WARN", t("health_check.runs_dry", job=job, field=key, days=limit)))
    return out


def check_worker_reach():
    """Mac-side reachability of the worker (tools/worker_reach.py, hourly).
    Absent state means this machine has no worker target configured."""
    path = os.path.join(VAULT, ".agents/state/worker_reach.json")
    if not os.path.exists(path):
        return
    label = f"{WORKER} reach"
    try:
        s = json.loads(open(path).read())
    except (OSError, ValueError):
        add(label, "RED", "Unreadable reachability state")
        return
    age = time.time() - s.get("checked_at", 0)
    fails = s.get("fails", 0)
    if age > 3 * 3600:
        add(label, "WARN", f"last check {ago(age)}; the hourly job may have stopped")
    elif fails:
        add(label, "RED" if fails >= 2 else "WARN", f"{fails} failed check(s): {s.get('reason', '')}")
    else:
        add(label, "OK", f"reachable, checked {ago(age)}")


def check_runs():
    """Per-run log (tools/run_log.py): a job that failed last time, went quiet,
    or ran every night with nothing to do. Exit codes miss the last two."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from run_log import read_rollup
    label = t("health_check.runs_label")
    findings, jobs = [], 0
    for host in ("mac", WORKER):
        roll = read_rollup(_runs_text(host))
        jobs += len({e["job"] for e in roll})
        findings += [(s, f"{host}: {msg}") for s, msg in run_findings(roll)]
    if not jobs:
        return  # no run log yet on either machine
    if findings:
        add(label, "WARN", "; ".join(msg for _, msg in findings[:4])
            + (f" (+{len(findings) - 4})" if len(findings) > 4 else ""))
    else:
        add(label, "OK", t("health_check.runs_ok", jobs=jobs))


def check_backup():
    """The monthly encrypted archive (tools/vault_archive.py) and its restore
    test. Sync copies damage everywhere; only an archive that sync cannot reach,
    and that has been restored once, protects against a bad automation run."""
    path = os.path.join(VAULT, ".agents", "state", "backup.json")
    try:
        with open(path) as fh:
            s = json.load(fh)
    except (OSError, ValueError):
        s = {}
    label = t("health_check.backup_label")
    if not s.get("created"):
        add(label, "WARN", t("health_check.backup_none"))
        return
    now = datetime.now()
    made = (now - datetime.fromisoformat(s["created"])).days
    ver = (now - datetime.fromisoformat(s["verified"])).days if s.get("verified") else None
    status = "RED" if made > 60 else "WARN" if made > 35 or ver is None or ver > 120 else "OK"
    add(label, status, t("health_check.backup_detail", made=made,
                         verified=t("health_check.backup_never") if ver is None else ver))


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


def check_privacy_guards():
    """Are the protected homes still ignored?

    A .gitignore rule that names one path stops matching the day the path is
    renamed, and nothing complains: the next compile simply tracks the file.
    That is how IBANs or a child's health records would reach the remote, so
    the check is cheap and runs every time rather than being remembered.
    """
    label = t("health_check.privacy_guards")
    exposed = []
    for rel in PROTECTED_HOMES:
        if not os.path.exists(os.path.join(VAULT, rel)):
            continue  # moved or renamed; the tracked-file scan below still covers it
        r = subprocess.run(["git", "check-ignore", "-q", rel], cwd=VAULT,
                           capture_output=True, timeout=30)
        if r.returncode != 0:
            exposed.append(rel)
    if exposed:
        add(label, "RED", t("health_check.privacy_exposed", paths=", ".join(exposed[:3])))
        return
    add(label, "OK", t("health_check.privacy_ok", count=len(PROTECTED_HOMES)))


def main():
    check_git()
    check_privacy_guards()
    # smart_processor runs hourly; 3h of silence means the cron line is dead.
    check_log(t("health_check.hourly_processor"), "logs/smart_processor.log", 3 * 3600, t("health_check.hourly_cron"))
    # Night jobs and telegram moved to the worker on 2026-08-26; they leave no
    # Mac log trace. The aggregate pulse is below, the detail is in the Linux watchdog.
    check_worker()
    check_worker_reach()
    check_dialectic()
    check_morning_briefing()
    check_kill_criteria()
    check_pile()
    check_graph()
    check_runs()
    check_backup()
    check_llm_auth()
    check_buzz_delivery()
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
        title = t("health_check.notify_title")
        if shutil.which("osascript"):  # macOS-only notification
            subprocess.run(
                ["osascript", "-e",
                 f'display notification "{reds}" with title "{title}"'],
                check=False,
            )
        elif shutil.which("notify-send"):  # Linux worker
            subprocess.run(["notify-send", title, reds], check=False)


if __name__ == "__main__":
    main()
