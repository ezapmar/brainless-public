"""One door to _Agent-Context/TASKS.md (tools/tasks_ledger.py, 2026-10-08).

Four writers used to read, edit and write the file with no lock; two at once
lost one side's rows. The ledger module locks, dedups and writes atomically.
"""
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
for d in (ROOT / "tools",):
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))
import tasks_ledger as tl  # noqa: E402

TEXT = "# Ledger\n\n## Promises\n\n- [ ] Call the supplier | [[2026-10-01]] | 2026-10-01\n- [x] Send the deck | [[2026-10-02]] | 2026-10-02\n\n## Waiting\n\n- [ ] Ayşe: contract draft | [[2026-10-03]] | 2026-10-03\n"


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name)
        (self.vault / "_Agent-Context").mkdir()
        self.path = self.vault / "_Agent-Context" / "TASKS.md"
        self.path.write_text(TEXT, encoding="utf-8")
        self.enterContext(mock.patch.dict(os.environ, {"BRAINLESS_VAULT": str(self.vault)}))

    def test_rows_and_titles(self):
        ledger = tl.read(self.path)
        rows = ledger.rows()
        self.assertEqual([(r.section, r.done, r.title) for r in rows],
                         [("promises", False, "Call the supplier"), ("promises", True, "Send the deck"),
                          ("waiting", False, "Ayşe: contract draft")])
        self.assertEqual(rows[0].source, "2026-10-01")
        self.assertEqual(ledger.titles(include_done=False), ["Call the supplier", "Ayşe: contract draft"])

    def test_add_goes_to_the_section_end_and_dedups(self):
        with tl.edit(self.path) as ledger:
            self.assertTrue(ledger.add("promises", "Book the venue", "2026-10-08", "2026-10-08"))
            self.assertFalse(ledger.add("promises", "call the SUPPLIER", "x", "2026-10-08"))
            self.assertFalse(ledger.add("promises", "Send the deck", "x", "2026-10-08"))     # done rows count
            self.assertTrue(ledger.add("promises", "Send the deck", "x", "2026-10-08", include_done=False))
        text = self.path.read_text(encoding="utf-8")
        promises = text.split("## Waiting")[0]
        self.assertIn("- [ ] Book the venue | [[2026-10-08]] | 2026-10-08\n", promises)
        self.assertTrue(promises.rstrip().endswith("| 2026-10-08"))
        self.assertEqual(text.count("Send the deck"), 2)

    def test_missing_section_and_missing_file(self):
        with tl.edit(self.path) as ledger:
            ledger.set_text("# Ledger\n")
            ledger.add("waiting", "Bank: statement", "", "2026-10-08")
        heading = tl.headings("waiting")[0]      # in the vault's own language
        self.assertIn(f"{heading}\n- [ ] Bank: statement |  | 2026-10-08\n", self.path.read_text(encoding="utf-8"))
        fresh = self.vault / "_Agent-Context" / "NEW.md"
        with tl.edit(fresh) as ledger:
            ledger.add("promises", "First ever", "", "2026-10-08")
        self.assertIn(tl.headings("promises")[0], fresh.read_text(encoding="utf-8"))
        self.assertIn("First ever", fresh.read_text(encoding="utf-8"))

    def test_mark_done(self):
        with tl.edit(self.path) as ledger:
            self.assertTrue(ledger.mark("Call the supplier"))
            self.assertFalse(ledger.mark("Call the supplier"))
            self.assertFalse(ledger.mark("no such row"))
        self.assertIn("- [x] Call the supplier", self.path.read_text(encoding="utf-8"))

    def test_unchanged_ledger_is_not_rewritten(self):
        before = self.path.stat().st_mtime_ns
        time.sleep(0.01)
        with tl.edit(self.path) as ledger:
            ledger.add("promises", "Call the supplier")
        self.assertEqual(self.path.stat().st_mtime_ns, before)

    def test_two_writers_keep_both_rows(self):
        """A writer that holds the lock blocks the other; neither row is lost."""
        started, order = threading.Event(), []

        def slow_writer():
            with tl.edit(self.path) as ledger:
                started.set()
                time.sleep(0.3)
                ledger.add("promises", "Renew the office lease", "", "2026-10-08")
                order.append("slow")

        def fast_writer():
            started.wait()
            with tl.edit(self.path) as ledger:
                ledger.add("promises", "Book the dentist", "", "2026-10-08")
                order.append("fast")

        a, b = threading.Thread(target=slow_writer), threading.Thread(target=fast_writer)
        a.start()
        b.start()
        a.join()
        b.join()
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("Renew the office lease", text)
        self.assertIn("Book the dentist", text)
        self.assertEqual(order, ["slow", "fast"])


if __name__ == "__main__":
    unittest.main()
