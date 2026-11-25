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

from paths import vault_root
VAULT = vault_root()
from owner_profile import PROTECTED_HOMES as profile_protected_homes
from owner_profile import CLOUD_EXCLUDE as profile_cloud_exclude
from i18n import t
import mac_notify
HEALTH_FILE = os.path.join(VAULT, "_Agent-Context", "HEALTH.md")
RED_FLAG_SECONDS = 2 * 24 * 3600
PILE_INBOX_RED = 10  # tools/wiki_prune.py INBOX_STALE_RED, the belief's own criterion

CHECKS = []  # (label, status, detail) with status in {OK, WARN, RED}
# Labels of rows that report on the owner's projects, not on the engine. They
# get their own line and stay out of the overall status: 17 overdue kill
# criteria kept the whole report RED for weeks (11/2026), so RED stopped
# meaning "a job is broken" and a real engine failure had nowhere to show.
PROJECT_ROWS = set()


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
# Folders whose content must never reach a cloud provider (P0.4). Same source of
# truth as the compiler and the model gate: PROFILE.md.
CLOUD_EXCLUDE = list(profile_cloud_exclude)
EGRESS_FILE = os.path.join(VAULT, ".agents", "state", "egress.jsonl")


def check_notify():
    """Telegram poller and outbox (telegram_capture.py runs interactions.py
    every tick). A laptop that slept is quiet, not broken: RED only past the
    2-day rule."""
    path = os.path.join(VAULT, ".agents/state/interactions_status.json")
    if not os.path.exists(path):
        add("Telegram", "WARN", "no poll recorded yet")
        return
    try:
        status = json.loads(open(path, encoding="utf-8").read())
        age = time.time() - status.get("checked_at", 0)
        pending = status.get("pending_messages", 0) + status.get("pending_replies", 0)
        level = "RED" if age > RED_FLAG_SECONDS else ("WARN" if age > 3600 or pending or status.get("failures") else "OK")
        add("Telegram", level, f"{pending} pending; last completed poll {ago(age)}")
    except (OSError, ValueError, TypeError):
        add("Telegram", "RED", "Unreadable interaction status")


def check_night():
    """The night shift (.agents/scripts/night.sh) stamps night_last.json when its
    chain ends. The Mac travels, so one missed night is normal; two is not."""
    path = os.path.join(VAULT, ".agents/state/night_last.json")
    label = "Night shift"
    try:
        s = json.loads(open(path, encoding="utf-8").read())
    except (OSError, ValueError):
        add(label, "WARN", "no completed night run recorded yet")
        return
    age = time.time() - s.get("finished_at", 0)
    failed = s.get("failed") or []
    status = "RED" if age > RED_FLAG_SECONDS else ("WARN" if failed else "OK")
    add(label, status, f"last finished {ago(age)}" + (f"; failed: {', '.join(failed[:5])}" if failed else ""))


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
        dirty = len([line for line in out.stdout.splitlines() if line.strip()])
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
        add(label, "RED", t(auth_key, ago=age, detail=detail[:70], worker="mac"))
    elif outcome == "timeout":
        add(label, "WARN", t("health_check.llm_timeout", ago=age))
    else:
        add(label, "WARN", t("health_check.llm_error", ago=age, detail=detail[:70]))


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
    else:
        try:
            with open(path, errors="replace") as fh:
                line = fh.read()
        except Exception:
            add(label, "WARN", t("health_check.status_file_unreadable"))
        else:
            llm_row(label, line, time.time() - os.path.getmtime(path))







def check_crm():
    """CRM snapshot canary (.agents/scripts/crm_capture.py).

    Silent when no CRM token is configured, so a clean install never shows red.
    Reads the breadcrumb the script drops on every run; log mtime alone would
    stay green while the API rejects the token every hour.
    """
    conf = os.path.expanduser("~/.config/brainless")
    try:
        with open(os.path.join(conf, "crm_provider"), encoding="utf-8") as fh:
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
    """The morning briefing is written by tools/morning_briefing.py in the
    morning set (until 19/11/2026 a Claude scheduled task that only fired while
    the desktop app was open, and failed silently). On weekdays after 08:30 the
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
    PROJECT_ROWS.add(label)
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
        with open(os.path.join(VAULT, "_Agent-Context", "PILE-SCORECARD.md"), errors="replace", encoding="utf-8") as fh:
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


def vault_graph_findings(data, age_s):
    """(status, detail) for the Obsidian-visible vault graph report (tools/vault_graph.py).
    Pure enough to test directly."""
    if age_s > RED_FLAG_SECONDS:
        return "RED", t("health_check.vault_graph_stale", age=ago(age_s))
    leaks = int(data.get("gitignore_leaks", 0))
    if leaks > 0:
        return "RED", t("health_check.vault_graph_leaks", leaks=leaks)
    import vault_graph
    res_view = {
        "gitignore_leak_count": leaks,
        "orphan_count": int(data.get("orphans", 0)),
        "components": int(data.get("components", 1)),
        "main_share": float(data.get("main_share", 1.0)),
        "cross_unexpected_count": int(data.get("cross_unexpected", 0)),
    }
    status = vault_graph.worst_status(res_view)
    detail = t(
        "health_check.vault_graph_detail",
        orphans=res_view["orphan_count"],
        components=res_view["components"],
        main=f"{res_view['main_share'] * 100:.1f}",
        cross=res_view["cross_unexpected_count"],
        leaks=leaks,
        age=ago(age_s),
    )
    return status, detail


def check_vault_graph():
    """Obsidian-visible vault connectivity (tools/vault_graph.py, nightly in night.sh).
    Tracks orphans, connected components, Work-Personal modularity and gitignore leaks."""
    label = t("health_check.vault_graph_label")
    path = os.path.join(VAULT, ".wiki", "_vault-graph-report.md")
    try:
        with open(path, errors="replace", encoding="utf-8") as fh:
            text = fh.read()
        m = re.search(r"<!-- vault_graph: (\{.*?\}) -->", text)
        data = json.loads(m.group(1)) if m else None
    except (OSError, ValueError):
        data = None
    if not data:
        add(label, "WARN", t("health_check.vault_graph_missing"))
        return
    try:
        age_s = time.time() - datetime.fromisoformat(str(data.get("generated_at", ""))).timestamp()
    except ValueError:
        age_s = time.time() - os.path.getmtime(path)
    status, detail = vault_graph_findings(data, age_s)
    add(label, status, detail)


_LINT_FIELDS = {"broken": r"broken links \(real\): (\d+)", "orphans": r"orphan files: (\d+)",
                "stale": r"stale summaries: (\d+)", "fm": r"frontmatter issues: (\d+)"}


def check_lint():
    """Wiki integrity from .wiki/_lint-report.md, rewritten every night by
    night.sh (report only; the Sunday run also fixes). Broken links and orphans
    are a backlog and only inform: a row that is permanently red is a row nobody
    reads. A stale report, a stale summary or missing frontmatter is a fault,
    because the first means the night did not lint and the other two mean a page
    the compiler owns is out of step with its source."""
    label = t("health_check.lint_label")
    path = os.path.join(VAULT, ".wiki", "_lint-report.md")
    try:
        with open(path, errors="replace", encoding="utf-8") as fh:
            head = fh.read(2000)
    except OSError:
        add(label, "WARN", t("health_check.lint_missing"))
        return
    n = {}
    for key, pattern in _LINT_FIELDS.items():
        m = re.search(pattern, head)
        n[key] = int(m.group(1)) if m else 0
    m = re.search(r"_Generated ([\dT:-]+)_", head)
    try:
        age = time.time() - datetime.fromisoformat(m.group(1)).timestamp()
    except (AttributeError, ValueError):
        add(label, "WARN", t("health_check.lint_missing"))
        return
    if age > RED_FLAG_SECONDS:
        add(label, "RED", t("health_check.lint_stale", age=ago(age)))
        return
    status = "WARN" if (n["stale"] or n["fm"]) else "OK"
    add(label, status, t("health_check.lint_detail", broken=n["broken"], orphans=n["orphans"],
                         stale=n["stale"], fm=n["fm"], age=ago(age)))




# Capture paths that should produce something within these many days; zeros
# for longer mean the path is broken, not that the owner was quiet.
DRY_CAPTURE = {("nightly_processor", "captures"): 3, ("spiky_capture", "reports"): 7,
               # A week of compiles that change no page is a stuck compiler.
               ("nightly_compile", "wiki_changed"): 7}


def job_exists(job):
    """A job is named after its script (run_log.job_name). No script, no job:
    it was retired, and its last rows sit in the window for two more weeks."""
    return any(os.path.exists(os.path.join(VAULT, d, job + ext))
               for d in ("tools", ".agents/scripts") for ext in (".py", ".sh"))


def run_findings(roll, now=None, exists=None):
    """(status, text) findings from a run-log rollup (tools/run_log.py).
    `exists(job)` says whether the job still has a script; a retired job is
    never reported as silent."""
    now = now or datetime.now()
    by_job = {}
    for e in roll:
        by_job.setdefault(e["job"], []).append(e)
    out = []
    for job, days in sorted(by_job.items()):
        days.sort(key=lambda e: e["day"])
        last = days[-1]
        if last["status"] == "fail":
            out.append(("WARN", t("health_check.runs_failed", job=job, day=last["day"], time=last["last"])))
        elif last["status"] == "partial":
            # A compile whose every model call failed exits cleanly and says
            # partial; on 24/09/2026 that read as a quiet night.
            out.append(("WARN", t("health_check.runs_partial", job=job, day=last["day"], time=last["last"])))
        # Expected gap from the job's own active-day cadence: counting distinct
        # active days instead of total runs keeps a one-off burst of manual or
        # test runs on a single day from making a weekly job look daily.
        if len(days) >= 2:
            span = (datetime.strptime(last["day"], "%Y-%m-%d")
                    - datetime.strptime(days[0]["day"], "%Y-%m-%d")).days or 1
            gap_days = max(1.0, span / (len(days) - 1))
            quiet = (now - datetime.strptime(f"{last['day']} {last['last']}", "%Y-%m-%d %H:%M")).total_seconds() / 86400
            if quiet > 2.5 * gap_days and (exists is None or exists(job)):
                out.append(("WARN", t("health_check.runs_quiet", job=job, days=f"{quiet:.1f}")))
        if last["counts"].get("screen_missed"):
            out.append(("WARN", t("health_check.runs_screen_missed", job=job, n=last["counts"]["screen_missed"])))
        if last["counts"].get("contradictions_skipped"):
            out.append(("WARN", t("health_check.runs_contradictions_skipped", job=job)))
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




def check_runs():
    """Per-run log (tools/run_log.py): a job that failed last time, went quiet,
    or ran every night with nothing to do. Exit codes miss the last two."""
    from run_log import read_rollup
    label = t("health_check.runs_label")
    try:
        with open(os.path.join(VAULT, "_Agent-Context/RUNS-mac.md"), errors="replace") as fh:
            roll = read_rollup(fh.read())
    except OSError:
        roll = []
    jobs = len({e["job"] for e in roll})
    findings = list(run_findings(roll, exists=job_exists))
    if not jobs:
        return  # no run log yet
    if findings:
        add(label, "WARN", "; ".join(msg for _, msg in findings[:4])
            + (f" (+{len(findings) - 4})" if len(findings) > 4 else ""))
    else:
        add(label, "OK", t("health_check.runs_ok", jobs=jobs))


def check_backup():
    """The weekly encrypted archive (tools/vault_archive.py) and its restore
    test. Sync copies damage everywhere; only an archive that sync cannot reach,
    and that has been restored once, protects against a bad automation run."""
    path = os.path.join(VAULT, ".agents", "state", "backup.json")
    try:
        with open(path, encoding="utf-8") as fh:
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
    # Weekly archive (night.sh, Sunday): one missed week is yellow, three are
    # red. The restore test is done by hand with the offline key, once a quarter.
    status = "RED" if made > 21 else "WARN" if made > 9 or ver is None or ver > 90 else "OK"
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
            last_run = max((i for i, line in enumerate(tail)
                            if "backed off" in line.lower()), default=-1)
            errs = [line.strip() for line in tail[last_run + 1:]
                    if any(k in line.lower() for k in ("error", "failed", "err]", "exception"))
                    and "0 failed" not in line]
            if errs:
                add(label, "WARN", t("health_check.error_lines", n=len(errs), last=errs[-1][:120]))
            else:
                add(label, "OK", t("health_check.tail_clean"))
        except Exception:
            pass






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


def routing_findings():
    """(status, detail) for model routing drift. Pure enough to test: reads env
    through llm, which has applied brainless.toml at import."""
    import llm
    cfg = os.path.join(VAULT, "brainless.toml")
    if not os.path.isfile(cfg):
        return "RED", t("health_check.routing_no_config")
    lanes = llm.local_only_lanes()
    if not lanes:
        return "RED", t("health_check.routing_empty")
    unknown = sorted(lanes - set(llm.LANES))
    if unknown:
        return "RED", t("health_check.routing_unknown", lanes=", ".join(unknown))
    cloud = sorted(l for l in lanes if not llm._is_local(llm.resolve_provider(l)))
    if cloud:
        return "RED", t("health_check.routing_drift", lanes=", ".join(cloud))
    return "OK", t("health_check.routing_ok", count=len(lanes))


def check_routing():
    """Every local-only lane still resolves to a local provider. A lane that
    drifts to the cloud is refused by llm.py, so this row is how the refusal
    gets noticed before a night of failed jobs."""
    try:
        status, detail = routing_findings()
    except Exception as e:  # a broken import must show, not hide the row
        status, detail = "RED", f"{type(e).__name__}: {e}"
    add(t("health_check.routing"), status, detail)


def privacy_findings(rep, baseline, gate):
    """(status, detail) for personal data in .wiki/. Pure, for the tests.

    0 files is OK. At or under the recorded baseline is WARN: known debt,
    cleared by redacting at ingest (P0.2), not by hand. Over the baseline is
    RED: a new leak, which the nightly gate holds back but which still means a
    source is writing personal data into the compiled layer."""
    held = (gate or {}).get("held") or {}
    tail = t("health_check.wiki_privacy_held", n=len(held), date=gate.get("date", "?")) if held else ""
    n = rep["files"]
    kinds = ", ".join(f"{k} {v}" for k, v in rep["kinds"].items())
    if n == 0:
        return ("WARN" if held else "OK"), t("health_check.wiki_privacy_ok") + tail
    if not baseline:
        return "WARN", t("health_check.wiki_privacy_no_baseline", n=n, kinds=kinds) + tail
    if n > baseline["files"]:
        return "RED", t("health_check.wiki_privacy_new", n=n, base=baseline["files"], kinds=kinds) + tail
    return "WARN", t("health_check.wiki_privacy_debt", n=n, base=baseline["files"], kinds=kinds) + tail


def check_wiki_privacy():
    try:
        import privacy_scan
        status, detail = privacy_findings(privacy_scan.scan_wiki(),
                                          privacy_scan.read_json(privacy_scan.BASELINE_FILE),
                                          privacy_scan.read_json(privacy_scan.GATE_FILE))
    except Exception as e:
        status, detail = "RED", f"{type(e).__name__}: {e}"
    add(t("health_check.wiki_privacy"), status, detail)


def read_egress(path=None):
    """The egress ledger as a list of rows. Missing file means no rows."""
    rows = []
    try:
        with open(path or EGRESS_FILE, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        pass
    return rows


def cloud_exclude_findings(exists=None, ledger=None, now=None, is_local=None):
    """(status, detail) for the cloud_exclude rule. Pure enough to test.

    Two failures, and the requirement is red either way:

      1. a listed folder is no longer on disk. A rename silently kills a
         path-based rule; the 2026-08-28 and 2026-09-19 incidents are exactly
         this shape, which is why the check runs every hour instead of being
         remembered.
      2. the ledger has a row from the last 24 hours that was sent to a cloud
         provider and carries `blocked: true`. The gate refuses such a call, so
         this row should be impossible; it is here as the second lock on the
         same door. The ledger holds no source path by design, so the flag is
         what makes the second check possible at all.
    """
    homes = CLOUD_EXCLUDE
    exists = exists or (lambda rel: os.path.isdir(os.path.join(VAULT, rel)))
    if not homes:
        return "WARN", t("health_check.cloud_exclude_empty")
    missing = [rel for rel in homes if not exists(rel)]
    if missing:
        return "RED", t("health_check.cloud_exclude_missing", paths=", ".join(missing[:3]))
    if is_local is None:
        import llm
        is_local = llm._is_local
    since = (now or datetime.now()) - timedelta(hours=24)
    leaked, refused = set(), 0
    for row in ledger or []:
        if str(row.get("phase") or "start") != "start":
            continue          # a completion line carries no send of its own
        try:
            stamp = datetime.strptime(str(row.get("ts", "")), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if stamp < since:
            continue
        if not row.get("sent", True):
            refused += row.get("blocked") is True   # nothing left the machine
            continue
        if is_local(str(row.get("provider", ""))):
            continue
        if row.get("blocked"):
            leaked.add(str(row.get("lane") or "-"))
    if leaked:
        return "RED", t("health_check.cloud_exclude_egress", lanes=", ".join(sorted(leaked)[:3]))
    if refused:
        return "WARN", t("health_check.cloud_exclude_refused", n=refused)
    return "OK", t("health_check.cloud_exclude_ok", count=len(homes))


def check_cloud_exclude():
    """A cloud_exclude folder still exists, and nothing from one hit the cloud."""
    try:
        status, detail = cloud_exclude_findings(ledger=read_egress())
    except Exception as e:  # a broken import must show, not hide the row
        status, detail = "RED", f"{type(e).__name__}: {e}"
    add(t("health_check.cloud_exclude"), status, detail)


def main():
    check_git()
    check_privacy_guards()
    check_routing()
    check_cloud_exclude()
    check_wiki_privacy()
    check_night()
    check_morning_briefing()
    check_kill_criteria()
    check_pile()
    check_graph()
    check_vault_graph()
    check_lint()
    check_runs()
    check_backup()
    check_llm_auth()
    check_notify()
    check_crm()
    check_log_errors()

    engine = [c for c in CHECKS if c[0] not in PROJECT_ROWS]
    projects = [c for c in CHECKS if c[0] in PROJECT_ROWS]
    worst = "OK"
    for _, status, _ in engine:
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
    ]
    marks = {"OK": "🟢", "WARN": "🟡", "RED": "🔴"}
    for label, status, detail in projects:
        lines += ["", t("health_check.projects_line", icon=marks[status], label=label, detail=detail)]
    lines.append("")
    for label, status, detail in engine:
        lines.append(f"- {marks[status]} {label}: {detail}")
    lines.append("")
    lines.append(t("health_check.footer"))

    os.makedirs(os.path.dirname(HEALTH_FILE), exist_ok=True)
    with open(HEALTH_FILE, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    if worst == "RED":
        reds = "; ".join(f"{label}: {d}" for label, s, d in engine if s == "RED")[:180]
        title = t("health_check.notify_title")
        if shutil.which("osascript"):
            # reds holds LLM error text and paths: argv only, never AppleScript source.
            subprocess.run(mac_notify.command(reds, title), check=False)
        try:  # once a day on the phone; the key makes repeats a no-op
            from notify import send
            send("ops", f"{title}\n{reds}", key="health:red:" + datetime.now().strftime("%Y-%m-%d"))
        except Exception:
            pass


if __name__ == "__main__":
    main()
