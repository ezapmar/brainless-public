#!/usr/bin/env python3
"""One log line for every tool: `[YYYY-MM-DD HH:MM:SS] message` on stdout.

Eighteen scripts defined this same function. It stays a print to stdout,
not the logging module, on purpose: the timers capture stdout into the
journal and the log files, run_log.py reads the RUNLOG line from the same
stream, and a second stream would split a run's story in two.

    from logline import log
    log("3 notes filed")                 # [2026-10-08 21:30:01] 3 notes filed
    log = logger("thinking_loop")        # [..] thinking_loop: 3 notes filed
"""
from datetime import datetime


def log(msg, *, prefix: str = "") -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {prefix}{msg}", flush=True)


def logger(name: str):
    """A log function whose lines carry `name: ` so several tools sharing one
    journal can be told apart."""
    def _log(msg):
        log(msg, prefix=f"{name}: ")
    return _log
