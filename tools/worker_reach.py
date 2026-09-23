#!/usr/bin/env python3
"""Can the Mac still reach the always-on worker? (Mac, hourly via cron_wrapper.sh)

On 22 and 23/09/2026 every ssh from the Mac to the worker timed out and nothing
said so; the cause was Tailscale stopped on the Mac. The worker's own watchdog
cannot report this: it posts to Buzz, and the Buzz relay sits on the same
tailnet. So the alarm is local: a macOS notification plus a HEALTH.md row.

Three checks, first failure wins:
  1. tailscale    Tailscale is running on this Mac.
  2. ssh          The worker answers ssh (BatchMode, 8 s).
  3. pulse        The worker committed to origin in the last PULSE_HOURS.
                  Its run log commits at least every 30 minutes, so a longer
                  gap means its timers stopped, even if ssh works.

The worker's ssh target is Mac-local config, never in git:
  ~/.config/brainless/worker_ssh   e.g. <user>@<worker-ip>
  (or BRAINLESS_WORKER_SSH). Without it the script does nothing.

State: .agents/state/worker_reach.json {checked_at, fails, reason, alerted}.
The notification fires once when fails reaches ALERT_AFTER, and once on
recovery after an alert. Always exits 0: a down worker is a finding, not a
crash of this job.
"""
import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from owner_profile import WORKER  # noqa: E402

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
STATE = os.path.join(VAULT, ".agents", "state", "worker_reach.json")
CONFIG = os.path.expanduser("~/.config/brainless/worker_ssh")
ALERT_AFTER = 2       # consecutive failed hourly runs
PULSE_HOURS = 2


def run(cmd, timeout=30):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=VAULT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(cmd, 1, "", str(exc))


def target():
    v = os.environ.get("BRAINLESS_WORKER_SSH", "").strip()
    if v:
        return v
    try:
        with open(CONFIG) as fh:
            return fh.read().strip()
    except OSError:
        return ""


def check_tailscale():
    if not shutil.which("tailscale"):
        return None
    r = run(["tailscale", "status", "--json"], timeout=10)
    try:
        state = json.loads(r.stdout).get("BackendState")
    except ValueError:
        state = None
    return None if state == "Running" else f"Tailscale is not running on this Mac ({state or 'no status'})"


def check_ssh(host):
    r = run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host, "true"], timeout=20)
    if r.returncode == 0:
        return None
    return f"{WORKER} does not answer ssh ({(r.stderr.strip().splitlines() or ['no output'])[-1][:120]})"


def check_pulse(now):
    run(["git", "fetch", "-q", "origin"], timeout=60)
    r = run(["git", "log", "origin/master", "--format=%ct\t%s", "-300"])
    for line in r.stdout.splitlines():
        ct, _, subject = line.partition("\t")
        if subject.rstrip().endswith(f"({WORKER})"):
            hours = (now - int(ct)) / 3600
            return None if hours <= PULSE_HOURS else f"{WORKER} has not committed for {hours:.1f} h"
    return f"no {WORKER} commit in the last 300 on origin"


def diagnose(host, now):
    return check_tailscale() or check_ssh(host) or check_pulse(now)


def load():
    try:
        with open(STATE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def notify(message):
    """Best-effort macOS notification; never raises."""
    msg = message.replace('"', "'")
    run(["osascript", "-e", f'display notification "{msg}" with title "brainless: {WORKER}"'], timeout=10)


def step(prev, reason, now):
    """-> (new state, notification text or None). Pure, for the tests."""
    fails = prev.get("fails", 0) + 1 if reason else 0
    alerted = prev.get("alerted", False)
    message = None
    if reason and fails >= ALERT_AFTER and not alerted:
        message, alerted = f"Unreachable for {fails} checks: {reason}", True
    elif not reason and alerted:
        message, alerted = f"Reachable again after {prev.get('fails', 0)} failed checks", False
    return {"checked_at": int(now), "fails": fails, "reason": reason or "", "alerted": alerted}, message


def main():
    host = target()
    if not host:
        print(f"no worker ssh target ({CONFIG}); skipping")
        return 0
    now = time.time()
    reason = diagnose(host, now)
    state, message = step(load(), reason, now)
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as fh:
        json.dump(state, fh)
    if message:
        notify(message)
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {reason or 'reachable'}")
    print(f"RUNLOG failures={state['fails']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
