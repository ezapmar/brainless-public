"""Slip-box addresses must be permanent, which makes every test here about not moving.

An id that changes is worse than no id: links and citations point at an address
that has silently become someone else's. So the tests are about stability under
the things that normally churn a vault, a rename, a recompile, a second run of
this tool, and about never touching an id that already exists.
"""
from datetime import datetime
from pathlib import Path
import os
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import zk_id  # noqa: E402

NOTE = """---
date: 2026-08-13
type: idea
status: seed
---

# Zorlanmayan konvansiyon gerçekte yoktur

## The Idea
Bir kural yaptırımı yoksa yalnızca bir temennidir.
"""


def write(tmp, name, text):
    path = Path(tmp) / name
    path.write_text(text)
    return path


class TestReading(unittest.TestCase):
    def test_finds_an_existing_id(self):
        self.assertEqual(zk_id.existing_id("---\nzk: 202608131230\n---\n"), "202608131230")

    def test_absent_id_is_none(self):
        self.assertIsNone(zk_id.existing_id(NOTE))

    def test_a_date_inside_the_body_is_not_an_id(self):
        self.assertIsNone(zk_id.existing_id("---\ndate: 2026-08-13\n---\n\n202608131230\n"))


class TestAssignment(unittest.TestCase):
    def test_id_comes_from_the_notes_own_date_not_the_clock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "idea.md", NOTE)
            when = zk_id.note_date(NOTE, path)
            self.assertEqual(when.strftime("%Y%m%d"), "20260813")

    def test_same_note_gets_the_same_id_on_every_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "idea.md", NOTE)
            first = zk_id.mint(zk_id.note_date(NOTE, path), set())
            second = zk_id.mint(zk_id.note_date(NOTE, path), set())
            self.assertEqual(first, second)

    def test_a_taken_address_pushes_the_next_one_forward(self):
        base = datetime(2026, 8, 13, 9, 0)
        first = zk_id.mint(base, set())
        second = zk_id.mint(base, {first})
        self.assertEqual(first, "202608130900")
        self.assertEqual(second, "202608130901")

    def test_a_broken_date_falls_back_instead_of_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = NOTE.replace("date: 2026-08-13", "date: not-a-date")
            path = write(tmp, "idea.md", text)
            self.assertIsInstance(zk_id.note_date(text, path), datetime)


class TestWriting(unittest.TestCase):
    def test_id_lands_under_the_date_line(self):
        out = zk_id.insert_id(NOTE, "202608130900")
        lines = out.splitlines()
        self.assertEqual(lines[1], "date: 2026-08-13")
        self.assertEqual(lines[2], "zk: 202608130900")

    def test_the_body_is_untouched(self):
        out = zk_id.insert_id(NOTE, "202608130900")
        self.assertIn("Bir kural yaptırımı yoksa yalnızca bir temennidir.", out)
        self.assertEqual(out.count("# Zorlanmayan konvansiyon gerçekte yoktur"), 1)

    def test_writing_is_idempotent(self):
        once = zk_id.insert_id(NOTE, "202608130900")
        self.assertIsNotNone(zk_id.existing_id(once))
        # The caller skips a note that already has an id; prove the guard works.
        self.assertEqual(zk_id.existing_id(once), "202608130900")

    def test_frontmatter_without_a_date_still_gets_an_id(self):
        text = "---\ntype: idea\n---\n\n# Untitled\n"
        out = zk_id.insert_id(text, "202608130900")
        self.assertIn("zk: 202608130900", out.split("---")[1])


class TestScanning(unittest.TestCase):
    def test_folder_readme_is_not_a_slip(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(Path(tmp) / "Thinking" / "Ideas")
            write(tmp, "Thinking/Ideas/README.md", "# Index\n")
            write(tmp, "Thinking/Ideas/real.md", NOTE)
            old = zk_id.VAULT
            zk_id.VAULT = Path(tmp)
            try:
                notes, _ = zk_id.collect(["Thinking/Ideas"])
            finally:
                zk_id.VAULT = old
            self.assertEqual([p.name for p in notes], ["real.md"])


if __name__ == "__main__":
    unittest.main()
