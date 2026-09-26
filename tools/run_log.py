#!/usr/bin/env python3
"""run_log.py: one line per scheduled run, with what the run actually did.

Exit codes only say a job did not crash. The failure that hides is the run
that completes and does nothing: the capture folder was empty for a week, the
model returned nothing and the job still exited 0, the compiler's own exit 1
was swallowed by its caller. So every run is recorded with its counts, and a
job whose counts stay at zero shows up in HEALTH.md like a crash would.

Usage:
  run_log.py exec [--job NAME] -- python3 tools/x.py [args]   run, stream, record
  run_log.py render                                            rebuild RUNS-<host>.md

A job reports its counts by printing one line anywhere in its output:
  RUNLOG captures=4 summaries=4 status=partial
`status` overrides ok/fail when the job knows better (a partial run exits 0).

Storage: raw runs in .agents/state/runs.jsonl (per machine, gitignored); a
14-day rollup in _Agent-Context/RUNS-<host>.md (tracked). Each host writes only
its own file, so two machines never race on one file in git.
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

VAULT = Path(os.environ.get("BRAINLESS_VAULT") or Path(__file__).resolve().parents[1])
STATE = VAULT / ".agents" / "state" / "runs.jsonl"
KEEP_DAYS = 30
SHOW_DAYS = 14
# Keys may carry digits (hit5): a key the pattern refused dropped the whole
# line, and the job was recorded with the previous line's counts instead.
RUNLOG_RE = re.compile(r"^RUNLOG((?:\s+[a-z_][a-z0-9_]*=\S+)+)\s*$")


def host() -> str:
    if os.environ.get("BRAINLESS_HOST"):
        return os.environ["BRAINLESS_HOST"]
    if sys.platform == "darwin":
        return "mac"
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from owner_profile import WORKER
        return WORKER
    except Exception:
        return "worker"


def runs_file(h: str | None = None) -> Path:
    return VAULT / "_Agent-Context" / f"RUNS-{h or host()}.md"


def parse_runlog(line: str) -> dict:
    """'RUNLOG a=1 b=x' -> {'a': 1, 'b': 'x'}; {} for any other line."""
    m = RUNLOG_RE.match(line.strip())
    if not m:
        return {}
    out = {}
    for pair in m.group(1).split():
        k, _, v = pair.partition("=")
        out[k] = int(v) if re.fullmatch(r"-?\d+", v) else v
    return out


def emit(**counts):
    """For Python jobs: print the RUNLOG line the wrapper reads."""
    print("RUNLOG " + " ".join(f"{k}={v}" for k, v in counts.items()), flush=True)


def job_name(cmd: list[str]) -> str:
    for part in cmd:
        if part.endswith((".py", ".sh")):
            return Path(part).stem
    return Path(cmd[0]).name if cmd else "unknown"


# ─── record and read ─────────────────────────────────────────────
def load(path: Path = STATE) -> list[dict]:
    rows = []
    try:
        for line in path.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return rows


def record(job: str, status: str, secs: float, counts: dict, *, now: datetime | None = None):
    now = now or datetime.now()
    row = {"ts": now.isoformat(timespec="seconds"), "host": host(), "job": job,
           "status": status, "secs": round(secs, 1), "counts": counts}
    rows = [r for r in load() if r.get("ts", "") >= (now - timedelta(days=KEEP_DAYS)).isoformat()]
    rows.append(row)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    tmp.replace(STATE)
    render(rows, now=now)
    return row


def rollup(rows: list[dict], *, now: datetime | None = None, days: int = SHOW_DAYS) -> list[dict]:
    """One entry per (day, job): runs, fails, last time and status, summed counts."""
    now = now or datetime.now()
    since = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    out = {}
    for r in sorted(rows, key=lambda r: r.get("ts", "")):
        day = r.get("ts", "")[:10]
        if day < since:
            continue
        e = out.setdefault((day, r["job"]), {"day": day, "job": r["job"], "runs": 0, "fails": 0,
                                             "last": "", "status": "", "counts": {}})
        e["runs"] += 1
        e["fails"] += r.get("status") == "fail"
        e["last"] = r["ts"][11:16]
        e["status"] = r.get("status", "")
        for k, v in (r.get("counts") or {}).items():
            if k == "status":
                continue
            if isinstance(v, int):
                e["counts"][k] = e["counts"].get(k, 0) + v
            else:
                e["counts"][k] = v
    return sorted(out.values(), key=lambda e: (e["day"], e["job"]), reverse=True)


def render(rows: list[dict] | None = None, *, now: datetime | None = None):
    rows = load() if rows is None else rows
    roll = rollup(rows, now=now)
    h = host()
    lines = [f"# Runs on {h}", "",
             "_Written by tools/run_log.py on every scheduled run. One row per job per day, "
             f"last {SHOW_DAYS} days. Do not edit by hand._", "",
             f"<!-- runs: {json.dumps(roll, ensure_ascii=False, separators=(',', ':'))} -->", "",
             "| Day | Job | Runs | Fails | Last | Status | Counts |",
             "|---|---|---|---|---|---|---|"]
    for e in roll:
        counts = " ".join(f"{k}={v}" for k, v in sorted(e["counts"].items())) or "-"
        lines.append(f"| {e['day']} | {e['job']} | {e['runs']} | {e['fails']} | {e['last']} | "
                     f"{e['status']} | {counts} |")
    path = runs_file(h)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    tmp.replace(path)


def read_rollup(text: str) -> list[dict]:
    m = re.search(r"<!-- runs: (\[.*?\]) -->", text)
    try:
        return json.loads(m.group(1)) if m else []
    except ValueError:
        return []


# ─── exec ────────────────────────────────────────────────────────
def run(cmd: list[str], job: str) -> int:
    start = time.monotonic()
    counts = {}
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors="replace", bufsize=1)
    except OSError as exc:
        print(f"run_log: cannot start {cmd[0]}: {exc}", file=sys.stderr)
        rc = 127
    else:
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            got = parse_runlog(line)
            if got:
                counts = got
        proc.stdout.close()
        rc = proc.wait()
    status = counts.pop("status", None) if isinstance(counts.get("status"), str) else None
    status = "fail" if rc != 0 else (status or "ok")
    try:
        record(job, status, time.monotonic() - start, counts)
    except Exception as exc:  # the log must never cost the job its exit code
        print(f"run_log: could not record: {exc}", file=sys.stderr)
    return rc


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["render"]:
        render()
        return 0
    if argv[:1] != ["exec"] or "--" not in argv:
        print(__doc__, file=sys.stderr)
        return 2
    sep = argv.index("--")
    opts, cmd = argv[1:sep], argv[sep + 1:]
    job = opts[opts.index("--job") + 1] if "--job" in opts else job_name(cmd)
    if not cmd:
        return 2
    return run(cmd, job)


if __name__ == "__main__":
    sys.exit(main())
