#!/usr/bin/env python3
"""Morning Today queue (worker, existing daily 08:00 reminder schedule).

At most one decision, one commitment, and one evidence review per day.
Repeated runs do not resend today's items.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from today_queue import send_queue
from net_wait import wait_for_network


def main():
    # A timer can fire right after wake-from-sleep, before the network is back.
    # Skip cleanly rather than crashing when the Telegram send raises; the queue
    # does not resend already-sent items, so the next run delivers today's items.
    if not wait_for_network():
        print("network not up yet (likely just woke); skipping this tick")
        return
    send_queue()


if __name__ == "__main__":
    main()
