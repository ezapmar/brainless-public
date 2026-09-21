#!/usr/bin/env python3
"""Buzz alert for a failed systemd unit.

Wired as `OnFailure=brainless-notify-failure@%n.service` on the brainless job
units: when a unit fails, systemd starts brainless-notify-failure@<unit>, which
runs this script with the failed unit's name. It reports the failure to Buzz
straight away, instead of waiting for the watchdog's periodic sweep to notice a
stale failed state.

Usage: notify_failure.py <failed-unit-name>

Alerts enter the durable Buzz outbox. Enqueue failures are logged without
cascading another failed unit.
"""
import os
import subprocess
import sys

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
sys.path.insert(0, os.path.join(VAULT, "tools"))
from i18n import t  # noqa: E402

CONF_DIR = os.path.expanduser("~/.config/brainless")
MAX_LOG = 1200  # bound journal excerpts


def read_conf(name):
    try:
        with open(os.path.join(CONF_DIR, name)) as fh:
            return fh.read().strip()
    except OSError:
        return ""


def journal_tail(unit, lines=12):
    try:
        r = subprocess.run(
            ["journalctl", "--user", "-u", unit, "-n", str(lines), "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=20)
        return r.stdout.strip()
    except Exception:
        return ""


def unit_status(unit):
    try:
        r = subprocess.run(
            ["systemctl", "--user", "show", unit,
             "-p", "Result", "-p", "ExecMainStatus", "-p", "ActiveState"],
            capture_output=True, text=True, timeout=20)
        return " ".join(r.stdout.split())
    except Exception:
        return ""


def send_buzz(text):
    from buzz_delivery import send
    return send("ops", text)


def main():
    unit = sys.argv[1] if len(sys.argv) > 1 else "unknown unit"
    status = unit_status(unit)
    log = journal_tail(unit)
    if len(log) > MAX_LOG:
        log = "..." + log[-MAX_LOG:]
    host = os.uname().nodename
    msg = f"\U0001F534 brainless: {unit} failed on {host}\n"
    if status:
        msg += f"{status}\n"
    if log:
        msg += f"\n{t('notify_failure.log_label')}\n{log}"
    ok = send_buzz(msg)
    if not ok:
        # A transient send failure (network down, Buzz unreachable) must not
        # leave this oneshot unit in `failed` state: the watchdog would then
        # report the notifier itself as a broken unit forever, an alert loop that
        # only a manual `systemctl --user reset-failed` clears. Log and exit 0.
        print(f"notify_failure: could not send alert for {unit}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # never cascade a failure out of the notifier
        print(f"notify_failure error: {exc}", file=sys.stderr)
        raise SystemExit(0)
