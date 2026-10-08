#!/usr/bin/env python3
"""An exclusive lock on an open file, on POSIX and on Windows.

    with open(path, "a", encoding="utf-8") as fh:
        lock_exclusive(fh)
        ...                      # released when the file closes

    try_lock_exclusive(fh) -> bool   # the same, but returns False at once when
                                     # another process holds it

POSIX uses flock. Windows has no fcntl, so it locks the first byte with msvcrt,
which blocks the same way; closing the file releases it on both.
"""
import time

try:
    import fcntl
except ImportError:          # Windows
    fcntl = None
    import msvcrt


def lock_exclusive(fh):
    if fcntl:
        fcntl.flock(fh, fcntl.LOCK_EX)
        return
    fh.seek(0)
    while True:
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)   # retries for ~10 s itself
            return
        except OSError:
            time.sleep(0.5)


def try_lock_exclusive(fh) -> bool:
    """Take the lock if free, else return False without waiting."""
    if fcntl:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False
    fh.seek(0)
    try:
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        return True
    except OSError:
        return False
