#!/usr/bin/env python3
"""_Agent-Context/TASKS.md, the one task ledger, through one door.

Four tools wrote the ledger with an unlocked read-modify-write and none of
them shared a lock: the nightly digest (nightly_processor), the Spiky action
extractor (spiky_actions, every 15 minutes), the Google Tasks sync
(gtasks_sync, every 30 minutes) and the Today queue (today_queue). Two of
them running at once lost one side's rows. Now every writer does

    with tasks_ledger.edit() as ledger:
        ledger.add("promises", "Call the supplier", "[[2026-10-08]]", "2026-10-08")

which takes an exclusive lock (vault_lock, portable), reads the file, and
writes it back atomically only if something changed. Readers that never
write use read().

Row format, one per line under a `## Promises` or `## Waiting` heading, in
whichever language the vault speaks:

    - [ ] title | [[source]] | YYYY-MM-DD

add() refuses a title the ledger already has, exactly (case folded) or as
the near duplicate task_dedup.is_duplicate knows, so a promise rephrased by
tonight's model does not become a second row.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import re

from fsutil import atomic_write
from i18n import t, t_list
from paths import vault_root
from task_dedup import is_duplicate
from vault_lock import lock_exclusive

SECTION_KEYS = ("promises", "waiting")
ROW_RE = re.compile(r"^- \[( |x)\] (.+?)\s*$")


def ledger_path(vault=None) -> Path:
    return Path(vault or vault_root()) / "_Agent-Context" / "TASKS.md"


def headings(key: str) -> list[str]:
    """Every heading line accepted for a section key: the vault's language and
    English, since older ledgers were written in English."""
    return ["## " + name for name in t_list(f"gtasks_sync.section_{key}")]


def section_key(line: str) -> str | None:
    s = line.strip()
    for key in SECTION_KEYS:
        if s in headings(key):
            return key
    return None


def template() -> str:
    return t("nightly_processor.ledger_template")


@dataclass
class Row:
    idx: int            # line index in the ledger
    section: str        # "promises" or "waiting"
    done: bool
    title: str
    source: str         # the middle column, without the [[ ]]
    date: str
    line: str

    @property
    def open(self) -> bool:
        return not self.done


class Ledger:
    def __init__(self, text: str):
        self.lines = text.splitlines(keepends=True)
        self.changed = False

    @property
    def text(self) -> str:
        return "".join(self.lines)

    def set_text(self, text: str) -> None:
        if text != self.text:
            self.lines = text.splitlines(keepends=True)
            self.changed = True

    def rows(self, key: str | None = None) -> list[Row]:
        out, current = [], None
        for i, line in enumerate(self.lines):
            if line.startswith("## "):
                current = section_key(line)
                continue
            m = ROW_RE.match(line.strip())
            if not m or current is None or (key and current != key):
                continue
            parts = [p.strip() for p in m.group(2).split(" | ")]
            out.append(Row(i, current, m.group(1) == "x", parts[0],
                           parts[1].strip("[]") if len(parts) > 1 else "",
                           parts[2] if len(parts) > 2 else "", line))
        return out

    def titles(self, *, include_done: bool = True, key: str | None = None) -> list[str]:
        return [r.title for r in self.rows(key) if include_done or not r.done]

    def has(self, title: str, *, include_done: bool = True) -> bool:
        """Exactly or nearly the same title already in the ledger."""
        known = self.titles(include_done=include_done)
        folded = title.casefold()
        return any(k.casefold() == folded for k in known) or is_duplicate(title, known)

    def _section_end(self, key: str) -> int:
        """Index at which a new row of this section goes; the section is
        created at the end of the file when the ledger has none."""
        start = next((i for i, line in enumerate(self.lines) if section_key(line) == key), None)
        if start is None:
            if self.lines and not self.lines[-1].endswith("\n"):
                self.lines[-1] += "\n"
            self.lines.append(("\n" if self.lines else "") + headings(key)[0] + "\n")
            self.changed = True
            return len(self.lines)
        end = start + 1
        while end < len(self.lines) and not self.lines[end].startswith("## "):
            end += 1
        while end > start + 1 and self.lines[end - 1].strip() == "":
            end -= 1            # above the blank line that ends the section
        return end

    def add(self, key: str, title: str, source: str = "", date: str = "", *,
            include_done: bool = True) -> bool:
        """Append a row unless the ledger already has the title. True when added."""
        title = " ".join(title.split())
        if key not in SECTION_KEYS:
            raise KeyError(key)
        if not title or self.has(title, include_done=include_done):
            return False
        src = source if not source or source.startswith("[[") else f"[[{source}]]"
        self.lines.insert(self._section_end(key), f"- [ ] {title} | {src} | {date}\n")
        self.changed = True
        return True

    def mark(self, title: str, done: bool = True) -> bool:
        """Tick (or untick) the row with this exact title. True when a line changed."""
        for r in self.rows():
            if r.title == title and r.done != done:
                old, new = ("- [ ]", "- [x]") if done else ("- [x]", "- [ ]")
                self.lines[r.idx] = r.line.replace(old, new, 1)
                self.changed = True
                return True
        return False


def read(path=None) -> Ledger:
    """The ledger as it is now, for readers; a missing file reads as the template."""
    p = Path(path or ledger_path())
    try:
        return Ledger(p.read_text(encoding="utf-8"))
    except OSError:
        return Ledger(template())


def _lock_path(p: Path) -> Path:
    return p.parents[1] / ".agents" / "state" / "tasks_ledger.lock"


@contextmanager
def edit(path=None):
    """Lock, read, yield the Ledger, write it back atomically if it changed."""
    p = Path(path or ledger_path())
    lock = _lock_path(p)
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "a", encoding="utf-8") as fh:
        lock_exclusive(fh)
        ledger = read(p)
        if not p.exists():
            ledger.changed = True
        yield ledger
        if ledger.changed:
            atomic_write(p, ledger.text)
