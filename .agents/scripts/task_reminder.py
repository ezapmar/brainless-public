#!/usr/bin/env python3
"""Morning Today queue (worker, existing daily 08:00 reminder schedule).

At most one decision, one commitment, and one evidence review per day.
Repeated runs do not resend today's items.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from today_queue import send_queue


def main():
    send_queue()


if __name__ == "__main__":
    main()
