"""evening_closeout.py says so when it writes nothing.

What must hold: with material for the day, a failed model call exits 1,
reports closeout=0 and creates no briefing file; a good call appends the
section, reports closeout=1 and exits 0; an idle day and a close-out already
in place are clean exits that write nothing.

Run: python3 -B -m unittest tools.tests.test_evening_closeout -v
"""
import contextlib
import io
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import evening_closeout as ec  # noqa: E402


class CloseoutCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.briefing = self.dir / f"daily-briefing-{datetime.now():%Y-%m-%d}.md"
        self.status = self.dir / "llm_status"
        self.start(patch.object(ec, "BRIEFING_DIR", str(self.dir)))
        self.start(patch.object(ec, "STATUS_FILE", str(self.status)))
        self.start(patch.object(ec, "post_to_buzz"))
        self.start(patch.object(sys, "argv", ["evening_closeout.py"]))

    def start(self, p):
        p.start()
        self.addCleanup(p.stop)

    def run_main(self, section, captures="a note", changes=""):
        out = io.StringIO()
        with patch.object(ec, "todays_captures", return_value=captures), \
                patch.object(ec, "todays_changes", return_value=changes), \
                patch.object(ec, "build_section", return_value=section), \
                contextlib.redirect_stdout(out):
            rc = ec.main()
        return rc, out.getvalue()

    def test_failed_model_call_fails_the_run(self):
        self.status.write_text("2026-10-03 21:05:00\ttimeout\t[closeout] claude-cli\n", encoding="utf-8")
        rc, out = self.run_main(None)
        self.assertEqual(rc, 1)
        self.assertIn("RUNLOG closeout=0", out)
        self.assertFalse(self.briefing.exists())

    def test_good_call_appends_the_section(self):
        self.briefing.write_text("# Morning\n", encoding="utf-8")
        rc, out = self.run_main("\n\n---\n\nthe section\n")
        self.assertEqual(rc, 0)
        self.assertIn("RUNLOG closeout=1", out)
        text = self.briefing.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# Morning\n"))
        self.assertIn("the section", text)

    def test_idle_day_is_a_clean_exit(self):
        rc, out = self.run_main("unused", captures="", changes="")
        self.assertEqual(rc, 0)
        self.assertIn("RUNLOG closeout=0", out)
        self.assertFalse(self.briefing.exists())

    def test_existing_closeout_is_a_clean_exit(self):
        before = f"# Morning\n\n{ec.MARKER}\n\nalready here\n"
        self.briefing.write_text(before, encoding="utf-8")
        rc, out = self.run_main("unused")
        self.assertEqual(rc, 0)
        self.assertIn("RUNLOG closeout=0", out)
        self.assertEqual(self.briefing.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
