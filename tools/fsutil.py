#!/usr/bin/env python3
"""Write a file so that readers see either the old text or the new one.

    atomic_write(path, text)

The text goes to a temporary file in the same directory and is moved over
the target with os.replace, which is atomic on POSIX and on Windows. Every
tool that writes a wiki page or a state file uses this: a crash, a timeout
or a `git add -A` from another job in the middle of a plain write_text
captured a truncated page (three tools had their own copy of the
tmp-then-replace dance, and sixteen write sites had none).
"""
import os
from pathlib import Path
import tempfile


def atomic_write(path, text: str, *, encoding: str = "utf-8") -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=path.suffix, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as stream:
            stream.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
