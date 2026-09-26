#!/usr/bin/env python3
"""A job queue for one machine that is not always on.

The full deployment runs thirty-odd systemd timers on an always-on worker. A
laptop has no such clock: it sleeps through 23:00, wakes on a train with no
network, and is shut for a week in August. So the lite profile has one entry
point, `brainless tick`, called by the OS every 15 minutes and at login, and
this queue decides what is due:

  1. Periodic jobs are enqueued from their last successful run, not from the
     clock, so a missed night runs at the next wake instead of being lost.
     Daily jobs run once per calendar day, from BRAINLESS_DAILY_HOUR (default
     21) on, or at once if a whole day was missed.
  2. The queue drains in order while the machine is online and the model is
     reachable. A job that needs neither (lint) runs offline.
  3. A failed job is retried with backoff (5 min, 30 min, 2 h, 6 h), then left
     as failed for `brainless queue` to show. A job stuck "running" for three
     hours (a crash, a sleep mid-run) is put back.

State is one SQLite file, .agents/state/jobs.sqlite3, so it needs no daemon and
no fcntl (Windows has none). One tick runs at a time: a lock file made with
O_EXCL, reclaimed when stale. Every job runs through run_log.py, so it shows up
in RUNS-<host>.md like the timer jobs do.

    brainless tick [--dry-run]           enqueue what is due, then drain
    brainless queue [list|add <kind>|retry|clear]
    brainless add "a thought"            capture a note; the evening digest reads it
    brainless add -                      the same, from stdin
    brainless add <file>                 copy a document to the inbox, queue a convert
"""
import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import folder, vault_root  # noqa: E402
import config  # noqa: E402,F401  brainless.toml before the env is read

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKOFF = (300, 1800, 7200, 21600)
STALE_RUNNING = 3 * 3600
STALE_LOCK = 3 * 3600

# kind -> (argv relative to the engine, needs_net, needs_llm, cadence).
# Order matters: when several are due in one tick they run in this order, so the
# digest reads the day's notes before the compile folds the digest in.
JOBS = {
    "convert": (["python3", ".agents/scripts/smart_processor.py"], False, False, "hourly"),
    "digest":  (["python3", "tools/nightly_processor.py"], True, True, "daily"),
    "compile": (["python3", "tools/nightly_compile.py"], True, True, "daily"),
    "lint":    (["python3", "tools/lint_wiki.py", "--fix", "--fix-links"], False, False, "weekly"),
    "prune":   (["python3", "tools/wiki_prune.py", "--count"], False, False, "weekly"),
}
CADENCE_SECONDS = {"hourly": 3600, "weekly": 7 * 86400}


def _state_dir():
    d = os.path.join(vault_root(), ".agents", "state")
    os.makedirs(d, exist_ok=True)
    return d


def connect():
    db = sqlite3.connect(os.path.join(_state_dir(), "jobs.sqlite3"), timeout=30,
                         isolation_level=None)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY,
            kind TEXT NOT NULL,
            args TEXT NOT NULL DEFAULT '[]',
            state TEXT NOT NULL DEFAULT 'queued',
            not_before REAL NOT NULL DEFAULT 0,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            created REAL NOT NULL,
            updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS periodic (
            kind TEXT PRIMARY KEY,
            last_run REAL NOT NULL);
    """)
    return db


# ─── queue primitives ─────────────────────────────────────────────

def enqueue(db, kind, args=(), now=None):
    """Queue a job unless the same kind and args already wait. Returns the id or None."""
    if kind not in JOBS:
        raise KeyError(f"unknown job kind: {kind}; one of {', '.join(JOBS)}")
    now = now or time.time()
    blob = json.dumps(list(args))
    db.execute("BEGIN IMMEDIATE")
    try:
        if db.execute("SELECT 1 FROM jobs WHERE kind=? AND args=? AND state IN ('queued','running')",
                      (kind, blob)).fetchone():
            db.execute("COMMIT")
            return None
        cur = db.execute("INSERT INTO jobs(kind,args,created,updated) VALUES (?,?,?,?)",
                         (kind, blob, now, now))
        db.execute("COMMIT")
        return cur.lastrowid
    except BaseException:
        db.execute("ROLLBACK")
        raise


def claim(db, can_net, can_llm, now=None):
    """Take the oldest runnable job and mark it running, atomically. None if none."""
    now = now or time.time()
    db.execute("BEGIN IMMEDIATE")
    try:
        rows = db.execute("SELECT * FROM jobs WHERE state='queued' AND not_before<=? ORDER BY id",
                          (now,)).fetchall()
        for row in rows:
            _, needs_net, needs_llm, _ = JOBS.get(row["kind"], (None, True, True, None))
            if (needs_net and not can_net) or (needs_llm and not can_llm):
                continue
            db.execute("UPDATE jobs SET state='running', updated=? WHERE id=?", (now, row["id"]))
            db.execute("COMMIT")
            return row
        db.execute("COMMIT")
        return None
    except BaseException:
        db.execute("ROLLBACK")
        raise


def finish(db, row, ok, error="", now=None):
    now = now or time.time()
    if ok:
        db.execute("UPDATE jobs SET state='done', last_error='', updated=? WHERE id=?", (now, row["id"]))
        db.execute("INSERT INTO periodic(kind,last_run) VALUES(?,?) "
                   "ON CONFLICT(kind) DO UPDATE SET last_run=excluded.last_run", (row["kind"], now))
        return
    attempts = row["attempts"] + 1
    if attempts > len(BACKOFF):
        db.execute("UPDATE jobs SET state='failed', attempts=?, last_error=?, updated=? WHERE id=?",
                   (attempts, error[:500], now, row["id"]))
    else:
        db.execute("UPDATE jobs SET state='queued', attempts=?, last_error=?, not_before=?, updated=? "
                   "WHERE id=?", (attempts, error[:500], now + BACKOFF[attempts - 1], now, row["id"]))


def requeue_stale(db, now=None):
    now = now or time.time()
    return db.execute("UPDATE jobs SET state='queued', updated=? WHERE state='running' AND updated<?",
                      (now, now - STALE_RUNNING)).rowcount


def forget_old(db, now=None, days=30):
    now = now or time.time()
    db.execute("DELETE FROM jobs WHERE state='done' AND updated<?", (now - days * 86400,))


# ─── what is due ──────────────────────────────────────────────────

def daily_hour():
    try:
        return min(max(int(os.environ.get("BRAINLESS_DAILY_HOUR", "21")), 0), 23)
    except ValueError:
        return 21


def is_due(cadence, last_run, now):
    """last_run None means never. Pure, so the catch-up rules are testable."""
    if last_run is None:
        return cadence != "daily" or now.hour >= daily_hour()
    if cadence == "daily":
        last = datetime.fromtimestamp(last_run).date()
        today = now.date()
        if last >= today:
            return False
        # Yesterday ran: wait for this evening. A whole day missed: run now.
        return now.hour >= daily_hour() or last < today - timedelta(days=1)
    return now.timestamp() - last_run >= CADENCE_SECONDS[cadence]


def due(db, now=None):
    """Periodic kinds whose cadence says run, in JOBS order. Changes nothing."""
    now = now or datetime.now()
    last = {r["kind"]: r["last_run"] for r in db.execute("SELECT * FROM periodic")}
    return [kind for kind, (_, _, _, cadence) in JOBS.items() if is_due(cadence, last.get(kind), now)]


def enqueue_due(db, now=None):
    now = now or datetime.now()
    return [kind for kind in due(db, now) if enqueue(db, kind, now=now.timestamp())]


# ─── readiness ────────────────────────────────────────────────────

def online():
    import net_wait
    return net_wait.network_up(timeout=3.0)


def llm_ready():
    """Can the configured provider plausibly answer? Cheap checks only: a local
    daemon that is down, or a CLI that is missing, keeps LLM jobs queued rather
    than burning an attempt. Cloud providers are left to the job's own error."""
    import llm
    provider = llm.resolve_provider("compile")
    if provider == "ollama" or (provider == "openai-compatible" and llm._is_local(provider)):
        return llm.list_models(provider) is not None
    if provider in ("claude-cli", "gemini-cli", "codex-cli", "goose"):
        from resolve_bin import resolve
        return bool(resolve({"claude-cli": "claude", "gemini-cli": "gemini",
                             "codex-cli": "codex"}.get(provider, provider)))
    return True


# ─── running ──────────────────────────────────────────────────────

def _argv(kind, args):
    base, *_ = JOBS[kind]
    cmd = [sys.executable if base[0] == "python3" else base[0], *base[1:], *args]
    return [sys.executable, os.path.join(ENGINE, "tools", "run_log.py"), "exec",
            "--job", f"lite-{kind}", "--", *cmd]


def run_job(row, timeout=None):
    """Run one claimed job; (ok, error text)."""
    timeout = timeout or int(os.environ.get("BRAINLESS_JOB_TIMEOUT", "5400"))
    env = dict(os.environ, BRAINLESS_VAULT=vault_root())
    try:
        r = subprocess.run(_argv(row["kind"], json.loads(row["args"])), cwd=ENGINE, env=env,
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s"
    except OSError as e:
        return False, f"{type(e).__name__}: {e}"
    if r.returncode != 0:
        tail = ((r.stderr or "") + (r.stdout or "")).strip().splitlines()[-3:]
        return False, f"exit {r.returncode}: " + " | ".join(tail)
    return True, ""


class Lock:
    """One tick at a time, with no fcntl: O_EXCL create, stale reclaim."""

    def __init__(self):
        self.path = os.path.join(_state_dir(), "tick.lock")
        self.fd = None

    def __enter__(self):
        try:
            if time.time() - os.path.getmtime(self.path) > STALE_LOCK:
                os.remove(self.path)
        except OSError:
            pass
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(self.fd, str(os.getpid()).encode())
        except FileExistsError:
            self.fd = None
        return self.fd is not None

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
            try:
                os.remove(self.path)
            except OSError:
                pass


def tick(dry_run=False, max_jobs=10, runner=run_job, now=None):
    """Enqueue what is due, then drain. Returns a summary dict (also printed)."""
    with Lock() as held:
        if not held:
            print("another tick is running")
            return {"skipped": True}
        db = connect()
        net = online()
        if dry_run:
            waiting = [r["kind"] for r in db.execute("SELECT kind FROM jobs WHERE state='queued' ORDER BY id")]
            fresh = [k for k in due(db, now) if k not in waiting]
            print(f"would queue: {', '.join(fresh) or 'nothing'}; already waiting: "
                  f"{', '.join(waiting) or 'none'}; online: {net}; model ready: {net and llm_ready()}")
            return {"added": fresh, "waiting": waiting, "online": net}
        requeue_stale(db)
        added = enqueue_due(db, now=now)
        ready = net and llm_ready()
        done, failed = [], []
        for _ in range(max_jobs):
            row = claim(db, can_net=net, can_llm=ready)
            if row is None:
                break
            ok, err = runner(row)
            finish(db, row, ok, err)
            (done if ok else failed).append(row["kind"])
        forget_old(db)
        left = db.execute("SELECT COUNT(*) FROM jobs WHERE state='queued'").fetchone()[0]
        print(f"RUNLOG queued={len(added)} ran={len(done)} failed={len(failed)} waiting={left}"
              + ("" if net else " status=offline"))
        return {"added": added, "done": done, "failed": failed, "waiting": left, "online": net}


# ─── capture ──────────────────────────────────────────────────────

def capture_text(text, now=None):
    """Write a note into the daily folder the evening digest reads."""
    import i18n
    now = now or datetime.now()
    text = text.strip()
    if not text:
        raise ValueError("empty note")
    lines = text.splitlines()
    title = lines[0][:80].strip("# ").strip() or "note"
    d = os.path.join(vault_root(), folder("daily"))
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, now.strftime("%Y-%m-%d-%H%M%S") + "-cli.md")
    n = 2
    while os.path.exists(path):
        path = os.path.join(d, now.strftime("%Y-%m-%d-%H%M%S") + f"-cli-{n}.md")
        n += 1
    if text.startswith("# "):
        body = text
    elif len(lines) == 1 and len(text) <= 80:
        body = f"# {title}"             # a one-line thought is its own title
    else:
        body = f"# {title}\n\n{text}"
    footer = i18n.t("jobqueue.capture_source", stamp=now.strftime("%Y-%m-%d %H:%M"))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"{body}\n\n---\n{footer}\n")
    return path


def capture_file(src, db=None):
    """Copy a document into the inbox and queue a convert."""
    if not os.path.isfile(src):
        raise FileNotFoundError(src)
    d = os.path.join(vault_root(), folder("inbox"))
    os.makedirs(d, exist_ok=True)
    stem, ext = os.path.splitext(os.path.basename(src))
    dst, n = os.path.join(d, stem + ext), 2
    while os.path.exists(dst):
        dst = os.path.join(d, f"{stem}-{n}{ext}")
        n += 1
    shutil.copy2(src, dst)
    enqueue(db or connect(), "convert")
    return dst


# ─── CLI ──────────────────────────────────────────────────────────

def _list(db):
    rows = db.execute("SELECT * FROM jobs WHERE state!='done' OR updated>? ORDER BY id",
                      (time.time() - 86400,)).fetchall()
    for r in rows:
        when = datetime.fromtimestamp(r["updated"]).strftime("%m-%d %H:%M")
        extra = f"  retry after {datetime.fromtimestamp(r['not_before']).strftime('%H:%M')}" \
            if r["state"] == "queued" and r["not_before"] > time.time() else ""
        err = f"  {r['last_error'][:80]}" if r["last_error"] else ""
        print(f"{r['id']:>5}  {r['kind']:<8} {r['state']:<7} {when}  tries={r['attempts']}{extra}{err}")
    for r in db.execute("SELECT * FROM periodic ORDER BY kind"):
        print(f"last {r['kind']:<8} {datetime.fromtimestamp(r['last_run']).strftime('%Y-%m-%d %H:%M')}")
    if not rows:
        print("queue empty")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="brainless", description="Single-machine job queue.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tick", help="enqueue what is due, then run it")
    t.add_argument("--dry-run", action="store_true")
    q = sub.add_parser("queue", help="show or change the queue")
    q.add_argument("action", nargs="?", default="list", choices=["list", "add", "retry", "clear"])
    q.add_argument("kind", nargs="?")
    a = sub.add_parser("add", help="capture a note, or a file into the inbox")
    a.add_argument("text", nargs="+")
    ns = ap.parse_args(argv)

    if ns.cmd == "tick":
        res = tick(dry_run=ns.dry_run)
        return 1 if res.get("failed") else 0
    if ns.cmd == "add":
        if ns.text == ["-"]:
            print(capture_text(sys.stdin.read()))
        elif len(ns.text) == 1 and os.path.isfile(ns.text[0]):
            print(capture_file(ns.text[0]))
        else:
            print(capture_text(" ".join(ns.text)))
        return 0
    db = connect()
    if ns.action == "add":
        if not ns.kind:
            ap.error(f"queue add needs a kind: {', '.join(JOBS)}")
        print("queued" if enqueue(db, ns.kind) else "already waiting")
    elif ns.action == "retry":
        n = db.execute("UPDATE jobs SET state='queued', attempts=0, not_before=0, updated=? "
                       "WHERE state='failed'" + (" AND kind=?" if ns.kind else ""),
                       (time.time(), ns.kind) if ns.kind else (time.time(),)).rowcount
        print(f"{n} job(s) back in the queue")
    elif ns.action == "clear":
        n = db.execute("DELETE FROM jobs WHERE state IN ('done','failed')").rowcount
        print(f"{n} finished job(s) removed")
    else:
        _list(db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
