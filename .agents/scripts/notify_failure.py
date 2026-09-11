#!/usr/bin/env python3
"""Telegram alert for a failed systemd unit.

Wired as `OnFailure=brainless-notify-failure@%n.service` on the brainless job
units: when a unit fails, systemd starts brainless-notify-failure@<unit>, which
runs this script with the failed unit's name. It reports the failure to Telegram
straight away, instead of waiting for the watchdog's periodic sweep to notice a
stale failed state.

Usage: notify_failure.py <failed-unit-name>

Reuses the same token/chat files as watchdog.py (~/.config/brainless/
telegram_token, telegram_chat_id). Best effort: never raises, so a notify
failure cannot cascade.
"""
import os
import subprocess
import sys
import urllib.parse
import urllib.request

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
sys.path.insert(0, os.path.join(VAULT, "tools"))
from i18n import t  # noqa: E402

CONF_DIR = os.path.expanduser("~/.config/brainless")
MAX_LOG = 1200  # keep the message well under Telegram's 4096 char limit


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


def send_telegram(text):
    token = read_conf("telegram_token")
    chat = read_conf("telegram_chat_id")
    if not (token and chat):
        print("no telegram token/chat configured", file=sys.stderr)
        return False
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    try:
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=30)
        return True
    except Exception as exc:
        print(f"telegram send failed: {exc}", file=sys.stderr)
        return False


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
    ok = send_telegram(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # never cascade a failure out of the notifier
        print(f"notify_failure error: {exc}", file=sys.stderr)
        raise SystemExit(1)
