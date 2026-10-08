"""smart_processor.py parks what it cannot read and cleans up after itself.

What must hold: a document with no text is 'unreadable', leaves no empty
_raw.md or folder behind, and is parked; a parked file is skipped until the
file itself changes; a transient failure still backs off; records of files
that are gone are pruned.

Run: python3 -B -m unittest tools.tests.test_processor_parking -v
"""
import contextlib
import importlib.util
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]

spec = importlib.util.spec_from_file_location(
    "smart_processor_parking", ROOT / ".agents/scripts/smart_processor.py"
)
processor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(processor)


class UnsupportedFormatException(Exception):
    """Same name as markitdown's; the processor matches on the name."""


class ParkingCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "Personal" / "scan.pdf"
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b"image only")
        self.work = self.source.with_suffix("")
        self.raw = self.work / "scan_raw.md"
        self.state = self.root / "failed.json"
        self.out = io.StringIO()
        for p in (patch.object(processor, "HIGH_VALUE_DIRS", []),
                  patch.object(processor, "TARGET_DIRS", [str(self.source.parent)]),
                  patch.object(processor, "QUARANTINE_FILE", str(self.state)),
                  patch.object(processor, "STATE_DIR", str(self.root)),
                  patch.object(processor, "git_sync"),
                  patch.object(processor, "process_inbox_images", return_value=(0, [], 0)),
                  patch.object(processor.subprocess, "run")):
            p.start()
            self.addCleanup(p.stop)
        self.enterContext(contextlib.redirect_stdout(self.out))
        p = patch.object(processor, "notify")
        self.notify = p.start()
        self.addCleanup(p.stop)

    def convert(self, effect):
        p = patch.object(processor, "convert_to_file", side_effect=effect)
        mock = p.start()
        self.addCleanup(p.stop)
        return mock

    @staticmethod
    def empty(src, dst):
        Path(dst).write_text("")

    def test_no_text_is_unreadable_and_leaves_nothing(self):
        self.convert(self.empty)
        self.assertEqual(processor.process_file(str(self.source)), "unreadable")
        self.assertFalse(self.raw.exists())
        self.assertFalse(self.work.exists())

    def test_unknown_format_is_unreadable(self):
        self.convert(UnsupportedFormatException("no converter"))
        self.assertEqual(processor.process_file(str(self.source)), "unreadable")
        self.assertFalse(self.work.exists())

    def test_other_errors_still_fail(self):
        self.convert(RuntimeError("disk hiccup"))
        self.assertEqual(processor.process_file(str(self.source)), "failed")

    def test_parked_file_is_skipped_until_it_changes(self):
        convert = self.convert(self.empty)
        processor.main()                       # first run parks it, says so once
        self.assertEqual(convert.call_count, 1)
        self.assertEqual(self.notify.call_count, 1)
        self.assertIn("parked=1", self.out.getvalue())
        self.assertTrue(processor.load_quarantine()[str(self.source)]["parked"])

        processor.main()                       # second run does not touch it
        self.assertEqual(convert.call_count, 1)
        self.assertEqual(self.notify.call_count, 1)

        self.source.write_bytes(b"a new scan, longer")
        processor.main()                       # a replaced file is tried again
        self.assertEqual(convert.call_count, 2)

    def test_transient_failure_backs_off_and_exits_nonzero(self):
        self.convert(RuntimeError("disk hiccup"))
        with self.assertRaises(SystemExit):
            processor.main()
        rec = processor.load_quarantine()[str(self.source)]
        self.assertEqual(rec["attempts"], 1)
        self.assertNotIn("parked", rec)

    def test_records_of_missing_files_are_pruned(self):
        gone = str(self.root / "Personal" / "renamed-away.pdf")
        processor.save_quarantine({gone: {"attempts": 5, "last_attempt": 1.0}})
        self.convert(lambda src, dst: Path(dst).write_text("text"))
        processor.main()
        self.assertNotIn(gone, processor.load_quarantine())

    def test_file_with_markdown_drops_its_old_record(self):
        self.work.mkdir()
        self.raw.write_text("a source card")
        os.utime(self.raw, (self.source.stat().st_mtime + 5,) * 2)
        processor.save_quarantine({str(self.source): {"attempts": 2, "last_attempt": 0}})
        convert = self.convert(self.empty)
        processor.main()
        self.assertEqual(convert.call_count, 0)
        self.assertEqual(processor.load_quarantine(), {})


if __name__ == "__main__":
    unittest.main()
