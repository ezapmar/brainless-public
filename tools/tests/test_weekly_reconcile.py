"""weekly_reconcile.py says so when it writes nothing.

What must hold: a failed model call exits 1, reports report=0 and leaves the
previous drift report alone; a good call writes the report, reports report=1
and exits 0.

Run: python3 -B -m unittest tools.tests.test_weekly_reconcile -v
"""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import weekly_reconcile as wr  # noqa: E402


class ReconcileCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        self.context = d / "CONTEXT.md"
        self.context.write_text("# Context\n", encoding="utf-8")
        self.report = d / "CONTEXT-DRIFT.md"
        self.report.write_text("last week's report\n", encoding="utf-8")
        self.status = d / "llm_status"
        for name, value in (("CONTEXT_FILE", str(self.context)), ("REPORT_FILE", str(self.report)),
                            ("STATUS_FILE", str(self.status))):
            p = patch.object(wr, name, value)
            p.start()
            self.addCleanup(p.stop)
        for name, value in (("last_week_briefings", "--- a briefing ---"), ("week_commits", "")):
            p = patch.object(wr, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(wr.shutil, "which", return_value=None)   # no desktop notification
        p.start()
        self.addCleanup(p.stop)

    def run_main(self, llm_result):
        out = io.StringIO()
        with patch.object(wr, "run_prompt", return_value=llm_result), contextlib.redirect_stdout(out):
            rc = wr.main()
        return rc, out.getvalue()

    def test_failed_model_call_fails_the_run(self):
        self.status.write_text("2026-09-27 20:05:00\ttimeout\t[reconcile] claude-cli\n", encoding="utf-8")
        rc, out = self.run_main(None)
        self.assertEqual(rc, 1)
        self.assertIn("RUNLOG report=0", out)
        self.assertIn("timeout", out)
        self.assertEqual(self.report.read_text(encoding="utf-8"), "last week's report\n")

    def test_good_call_writes_the_report(self):
        rc, out = self.run_main("## Drift\n- one item")
        self.assertEqual(rc, 0)
        self.assertIn("RUNLOG report=1", out)
        self.assertIn("one item", self.report.read_text(encoding="utf-8"))

    def test_unreadable_context_fails_the_run(self):
        self.context.unlink()
        rc, out = self.run_main("unused")
        self.assertEqual(rc, 1)
        self.assertIn("RUNLOG report=0", out)


if __name__ == "__main__":
    unittest.main()
