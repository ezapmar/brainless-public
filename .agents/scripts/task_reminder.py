#!/usr/bin/env python3
"""Sabah gorev hatirlaticisi (worker, her gun 08:00).

TASKS.md'deki acik maddelerden 2 gunu gecenleri Telegram'dan bildirir.
2 gun kurali: bekleyen is sessizce eskimesin. Hic eskiyen yoksa susar.
"""
import os
import re
import sys
from datetime import datetime, timedelta

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
sys.path.insert(0, os.path.join(VAULT, ".agents", "scripts"))
from watchdog import send_telegram

TASKS_FILE = os.path.join(VAULT, "_Agent-Context", "TASKS.md")
STALE_DAYS = 2


def main():
    try:
        with open(TASKS_FILE) as fh:
            lines = fh.read().splitlines()
    except OSError:
        return
    cutoff = (datetime.now() - timedelta(days=STALE_DAYS)).strftime("%Y-%m-%d")
    section, stale = None, {"Sözlerim": [], "Bekliyorum": []}
    for line in lines:
        s = line.strip()
        if s.startswith("## "):
            section = s[3:]
            continue
        m = re.match(r"- \[ \] (.+)", s)
        if m and section in stale:
            parts = [p.strip() for p in m.group(1).split(" | ")]
            date = parts[2] if len(parts) > 2 else ""
            if date and date <= cutoff:
                age = (datetime.now() - datetime.strptime(date, "%Y-%m-%d")).days
                stale[section].append(f"{parts[0]} ({age} gün)")

    if not (stale["Sözlerim"] or stale["Bekliyorum"]):
        print("Eskiyen madde yok")
        return
    msg = ["📋 Görev hatırlatması (2+ gündür açık):"]
    # En eski 8 madde gosterilir; gerisi sayiyla. Tek defter (Spiky + nightly)
    # oldugu icin liste uzayabilir; Telegram'i bogmadan "hala acik" sinyali verir.
    for key, label in (("Sözlerim", "Sözlerin"), ("Bekliyorum", "Beklediklerin")):
        items = stale[key]
        if not items:
            continue
        msg.append(f"\n{label} ({len(items)}):")
        msg += [f"- {t}" for t in items[:8]]
        if len(items) > 8:
            msg.append(f"- ... +{len(items) - 8} madde daha (TASKS.md / Google Tasks)")
    send_telegram("\n".join(msg))
    print(f"Hatirlatma gonderildi: {len(stale['Sözlerim'])} + {len(stale['Bekliyorum'])}")


if __name__ == "__main__":
    main()
