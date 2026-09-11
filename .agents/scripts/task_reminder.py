#!/usr/bin/env python3
"""Morning task reminder (worker, every day 08:00).

Reports the open items in TASKS.md that are older than 2 days via Telegram.
The 2-day rule: pending work must not age silently. Stays quiet when nothing is stale.
"""
import os
import re
import sys
from datetime import datetime, timedelta

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
sys.path.insert(0, os.path.join(VAULT, "tools"))
sys.path.insert(0, os.path.join(VAULT, ".agents", "scripts"))
from watchdog import send_telegram
from i18n import t, t_list  # noqa: E402

TASKS_FILE = os.path.join(VAULT, "_Agent-Context", "TASKS.md")
STALE_DAYS = 2
# Section headings of TASKS.md come from the gtasks_sync locale (single source);
# both the current language and English are accepted when parsing.
SECTION_ALIASES = {}
for _key in ("promises", "waiting"):
    for _name in t_list(f"gtasks_sync.section_{_key}"):
        SECTION_ALIASES[_name] = _key


def main():
    try:
        with open(TASKS_FILE) as fh:
            lines = fh.read().splitlines()
    except OSError:
        return
    cutoff = (datetime.now() - timedelta(days=STALE_DAYS)).strftime("%Y-%m-%d")
    section, stale = None, {"promises": [], "waiting": []}
    for line in lines:
        s = line.strip()
        if s.startswith("## "):
            section = SECTION_ALIASES.get(s[3:])
            continue
        m = re.match(r"- \[ \] (.+)", s)
        if m and section in stale:
            parts = [p.strip() for p in m.group(1).split(" | ")]
            date = parts[2] if len(parts) > 2 else ""
            if date and date <= cutoff:
                age = (datetime.now() - datetime.strptime(date, "%Y-%m-%d")).days
                stale[section].append(t("task_reminder.item_age", title=parts[0], age=age))

    if not (stale["promises"] or stale["waiting"]):
        print("No stale items")
        return
    msg = [t("task_reminder.header", days=STALE_DAYS)]
    # The oldest 8 items are shown; the rest as a count. Since it is a single
    # ledger (Spiky + nightly) the list can grow; this signals "still open"
    # without flooding Telegram.
    for key, label in (("promises", t("task_reminder.label_promises")),
                       ("waiting", t("task_reminder.label_waiting"))):
        items = stale[key]
        if not items:
            continue
        msg.append(f"\n{label} ({len(items)}):")
        msg += [f"- {x}" for x in items[:8]]
        if len(items) > 8:
            msg.append(t("task_reminder.more_items", n=len(items) - 8))
    send_telegram("\n".join(msg))
    print(f"Reminder sent: {len(stale['promises'])} + {len(stale['waiting'])}")


if __name__ == "__main__":
    main()
