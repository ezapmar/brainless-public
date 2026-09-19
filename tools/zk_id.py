#!/usr/bin/env python3
"""Slip-box addresses: report, and on request assign, the `zk:` id of a note.

A Zettelkasten note has a permanent address that survives every rename. In this
vault the address is a timestamp, `zk: YYYYMMDDHHmm`, written once into the
frontmatter and never changed again. `_Templates/Idea.md` mints one for notes
created from the template and `tools/compile_resources.py` preserves the id of
every compiled idea across rebuilds, so the machine side has always had
addresses. The hand-written side did not: on 2026-09-19 all ten notes in
`Thinking/Ideas/` were missing one, which meant the archive's permanent
addresses existed only in the layer that is regenerable anyway.

This tool closes that gap without breaking the ownership rule. `Thinking/` is a
human area, so the default is a report and nothing is written. `--apply` writes,
and is the owner's own act, not an agent's.

    python3 tools/zk_id.py                 # what is missing an address
    python3 tools/zk_id.py --check         # exit 1 if anything is missing
    python3 tools/zk_id.py --apply         # write the missing ids
    python3 tools/zk_id.py --dirs Thinking/Ideas Thinking/Beliefs

Assignment is deterministic and idempotent: the id comes from the note's own
`date:` frontmatter, not from the clock, so running this today and running it
next month produce the same address for the same note. An id already present is
never touched, and a collision walks forward one minute at a time.
"""
import argparse
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

VAULT = Path(os.environ.get("BRAINLESS_VAULT")
             or Path(__file__).resolve().parents[1])
DEFAULT_DIRS = ["Thinking/Ideas"]

_ZK = re.compile(r"^zk:\s*(\d{8,14})\s*$", re.M)
_DATE = re.compile(r"^date:\s*(\d{4}-\d{2}-\d{2})", re.M)
_FRONTMATTER = re.compile(r"\A---\s*\n(.*?\n)---\s*\n", re.S)
# The hour an inferred address lands on. Arbitrary, but fixed: the point of the
# id is that it never moves, so it must not depend on when this ran.
INFER_HOUR = 9


def existing_id(text: str) -> str | None:
    m = _ZK.search(text)
    return m.group(1) if m else None


def note_date(text: str, path: Path) -> datetime:
    """The note's own date, falling back to the file's mtime."""
    m = _DATE.search(text)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d").replace(hour=INFER_HOUR)
        except ValueError:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime).replace(
        hour=INFER_HOUR, minute=0, second=0, microsecond=0)


def mint(base: datetime, taken: set[str]) -> str:
    """The first free address at or after base, one minute at a time."""
    when = base
    for _ in range(60 * 24):
        candidate = when.strftime("%Y%m%d%H%M")
        if candidate not in taken:
            return candidate
        when += timedelta(minutes=1)
    raise RuntimeError(f"no free slip-id near {base:%Y-%m-%d}")


def insert_id(text: str, zk: str) -> str:
    """Put `zk:` into the frontmatter, under `date:` when there is one.

    A note with no frontmatter is left alone by the caller; inventing one here
    would rewrite a file this tool has no business restructuring.
    """
    m = _DATE.search(text)
    if m:
        end = text.index("\n", m.start()) + 1
        return text[:end] + f"zk: {zk}\n" + text[end:]
    fm = _FRONTMATTER.match(text)
    start = fm.start(1)
    return text[:start] + f"zk: {zk}\n" + text[start:]


def collect(dirs) -> tuple[list[Path], set[str]]:
    """Markdown notes under dirs, and every slip-id already used in the vault."""
    # A folder README indexes the slip-box, it is not a slip in it.
    notes = []
    for d in dirs:
        root = VAULT / d
        if not root.is_dir():
            continue
        notes += sorted(p for p in root.rglob("*.md")
                        if not p.name.startswith((".", "_"))
                        and p.name.lower() != "readme.md")
    taken = set()
    for p in VAULT.rglob("*.md"):
        if ".git" in p.parts:
            continue
        try:
            found = existing_id(p.read_text(errors="replace"))
        except OSError:
            continue
        if found:
            taken.add(found)
    return notes, taken


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dirs", nargs="+", default=DEFAULT_DIRS,
                    help=f"folders to scan (default: {' '.join(DEFAULT_DIRS)})")
    ap.add_argument("--apply", action="store_true",
                    help="write the missing ids (this edits your notes)")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if any note is missing an address")
    args = ap.parse_args()

    notes, taken = collect(args.dirs)
    missing, skipped = [], []
    for path in notes:
        text = path.read_text(errors="replace")
        if existing_id(text):
            continue
        if not _FRONTMATTER.match(text):
            skipped.append(path)
            continue
        missing.append((path, text))

    if not missing and not skipped:
        print(f"every note in {', '.join(args.dirs)} has a slip-box address")
        return 0

    for path, text in missing:
        zk = mint(note_date(text, path), taken)
        taken.add(zk)
        rel = path.relative_to(VAULT)
        if args.apply:
            path.write_text(insert_id(text, zk))
            print(f"wrote  zk: {zk}  {rel}")
        else:
            print(f"would write  zk: {zk}  {rel}")
    for path in skipped:
        print(f"skipped (no frontmatter)  {path.relative_to(VAULT)}")

    if not args.apply and missing:
        print(f"\n{len(missing)} note(s) without an address. "
              f"These are your notes, so nothing was written. "
              f"Run with --apply to assign them.")
    return 1 if (args.check and (missing or skipped)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
