#!/usr/bin/env python3
"""brainless watchdog (runs hourly on the worker).

Reports silent breakages via Buzz #ops. Checks:
  1. Mac silence: the last commit on origin/master without the worker signature
     is > 26 hours old (note: this is the last commit that REACHED GitHub; the
     Mac may be committing locally while its push fails, as on 2026-09-01/02)
  2. HEALTH.md red: the committed HEALTH.md status line shows the red icon
  3. Linux jobs: a brainless-* unit is in systemctl --user failed state
  4. LLM auth: the last LLM call on this machine returned an auth error
  5. Power (--power, every 5 minutes): the charger has been unplugged for
     POWER_GRACE_MINUTES, or the battery is below POWER_LOW_PERCENT and not
     charging. A laptop worker shuts down when its battery runs out.

The same topic is reported once per 12 hours (state: .agents/state/watchdog_state.json,
gitignored); power topics repeat every POWER_DEDUPE_HOURS while the problem lasts
(state: watchdog_power_state.json). It does not write to the vault or push; it
only reads and messages.
"""
import json
import os
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
import sys  # noqa: E402
sys.path.insert(0, os.path.join(VAULT, "tools"))
from owner_profile import WORKER  # noqa: E402
from i18n import t  # noqa: E402
CONF_DIR = os.path.expanduser("~/.config/brainless")
STATE_FILE = os.path.join(VAULT, ".agents", "state", "watchdog_state.json")
MAC_SILENCE_HOURS = 26
DEDUPE_HOURS = 12
POWER_STATE_FILE = os.path.join(VAULT, ".agents", "state", "watchdog_power_state.json")
POWER_SUPPLY_DIR = "/sys/class/power_supply"
POWER_GRACE_MINUTES = 10
POWER_LOW_PERCENT = 30
POWER_DEDUPE_HOURS = 1


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def read_file(path):
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return None


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, cwd=VAULT, **kw)


# Every producer supplies its category explicitly; delivery is persisted before
# a watchdog topic is marked reported. Telegram is capture-only.
def send_buzz(text, channel="ops"):
    from buzz_delivery import send
    return send(channel, text)


def check_buzz(issues):
    path = os.path.join(VAULT, ".agents/state/buzz_interactions_status.json")
    if not os.path.exists(path):
        return
    try:
        status = json.loads(read_file(path))
        if time.time() - status.get("checked_at", 0) > 900:
            issues["buzz-stale"] = "Buzz reply worker has not completed a poll for 15 minutes."
        if status.get("pending_messages") or status.get("pending_replies"):
            issues["buzz-pending"] = f"Buzz pending: {status.get('pending_messages', 0)} messages, {status.get('pending_replies', 0)} replies."
    except (ValueError, TypeError):
        issues["buzz-state"] = "Buzz interaction status is unreadable."


def check_mac_silence(issues):
    run(["git", "fetch", "-q", "origin"])
    r = run(["git", "log", "origin/master", "--format=%ct\t%s", "-100"])
    now = time.time()
    for line in r.stdout.splitlines():
        ct, _, subject = line.partition("\t")
        if not subject.rstrip().endswith(f"({WORKER})"):
            hours = (now - int(ct)) / 3600
            if hours > MAC_SILENCE_HOURS:
                issues["mac-silent"] = t("watchdog.mac_silent", hours=f"{hours:.0f}",
                                         threshold=MAC_SILENCE_HOURS)
            return
    issues["mac-silent"] = t("watchdog.mac_no_commit")


def check_health_red(issues):
    r = run(["git", "show", "origin/master:_Agent-Context/HEALTH.md"])
    # The status line is "**<label>: 🔴 RED**"; the label is localized by
    # health_check.py, so match on the bold marker and the red icon only.
    for line in r.stdout.splitlines():
        if line.startswith("**") and "🔴" in line:
            issues["health-red"] = t("watchdog.health_red", line=line.strip('* '))
            return


def check_failed_units(issues):
    r = subprocess.run(
        ["systemctl", "--user", "--failed", "--no-legend", "--plain"],
        capture_output=True, text=True)
    failed = [l.split()[0] for l in r.stdout.splitlines()
              if "brainless-" in l or "buzz-" in l]
    if failed:
        issues["units-failed"] = t("watchdog.units_failed", units=", ".join(failed))


def check_dialectic(issues):
    """The dialectic round (12:30 and 21:20) writes one line per run into
    DIALECTIC-STATUS.md (idle included). No noon line after 14:00 or no evening
    line after 22:30 means the round did not run."""
    # No alarm before the engine is installed: no timer means this feature is
    # not enabled yet (install_personas.sh has not run). Never warn about a
    # round that was never switched on.
    if not os.path.exists(os.path.expanduser(
            "~/.config/systemd/user/brainless-dialectic.timer")):
        return
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    text = read_file(os.path.join(VAULT, "_Agent-Context", "DIALECTIC-STATUS.md")) or ""
    lines = [l for l in text.splitlines() if l.startswith(f"- {today} ")]
    have = {l.split()[2].rstrip(":") for l in lines}
    errors = [l for l in lines if ": error," in l]
    missing = []
    if now.hour * 60 + now.minute >= 14 * 60 and "noon" not in have:
        missing.append("noon (12:30)")
    if now.hour * 60 + now.minute >= 22 * 60 + 30 and "evening" not in have:
        missing.append("evening (21:20)")
    if missing:
        issues["dialectic-missing"] = t("watchdog.dialectic_missing", missing=", ".join(missing))
    if errors:
        issues["dialectic-error"] = t("watchdog.dialectic_error", error=errors[-1][2:120])


def check_llm_auth(issues):
    s = read_file(os.path.join(VAULT, ".agents", "state", "llm_status"))
    if s and "\tauth\t" in s:
        # Do not carry raw CLI stderr to Buzz; env/argv/credentials could leak.
        # Report only the timestamp (first field), drop the detail field.
        stamp = s.split("\t", 1)[0]
        issues["llm-auth"] = t("watchdog.llm_auth", stamp=stamp)


def check_power(issues, state, now):
    """Mains or USB-C power online counts as plugged in. The first unplugged
    sighting is kept in state, so a brief unplug inside the grace window stays
    quiet. Machines without a battery are skipped."""
    supplies = {}
    for name in sorted(os.listdir(POWER_SUPPLY_DIR)):
        path = os.path.join(POWER_SUPPLY_DIR, name)
        supplies[name] = (read_file(os.path.join(path, "type")),
                          read_file(os.path.join(path, "online")),
                          read_file(os.path.join(path, "capacity")),
                          read_file(os.path.join(path, "status")))
    batteries = [s for s in supplies.values() if s[0] == "Battery" and s[2]]
    if not batteries:
        return
    plugged = any(s[0] in ("Mains", "USB") and s[1] == "1" for s in supplies.values())
    capacity = min(int(b[2]) for b in batteries)
    charging = any(b[3] in ("Charging", "Full") for b in batteries)
    if plugged:
        state.pop("unplugged_since", None)
    else:
        since = state.setdefault("unplugged_since", now)
        minutes = (now - since) / 60
        if minutes >= POWER_GRACE_MINUTES:
            issues["power-unplugged"] = t("watchdog.power_unplugged",
                                          minutes=f"{minutes:.0f}", capacity=capacity)
    if capacity < POWER_LOW_PERCENT and not charging:
        issues["power-battery-low"] = t("watchdog.power_battery_low",
                                        capacity=capacity, threshold=POWER_LOW_PERCENT)


def main():
    power_mode = "--power" in sys.argv[1:]
    state_file = POWER_STATE_FILE if power_mode else STATE_FILE
    dedupe_hours = POWER_DEDUPE_HOURS if power_mode else DEDUPE_HOURS
    try:
        state = json.loads(read_file(state_file) or "{}")
    except ValueError:
        state = {}
    now = time.time()

    issues = {}
    if power_mode:
        try:
            check_power(issues, state, now)
        except Exception as e:
            log(f"check_power error: {e}")
    else:
        for check in (check_mac_silence, check_health_red, check_failed_units,
                      check_llm_auth, check_dialectic, check_buzz):
            try:
                check(issues)
            except Exception as e:
                log(f"{check.__name__} error: {e}")

    fresh = {k: v for k, v in issues.items()
             if now - state.get(k, 0) > dedupe_hours * 3600}

    if fresh:
        text = t("watchdog.header") + "\n".join(f"- {v}" for v in fresh.values())
        if send_buzz(text):
            log(f"Alert sent: {list(fresh)}")
            state.update({k: now for k in fresh})
    else:
        log(f"Clean ({len(issues)} known topics)" if issues else "Clean")

    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    with open(state_file, "w") as fh:
        json.dump(state, fh)


if __name__ == "__main__":
    main()
