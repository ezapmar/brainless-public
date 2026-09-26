"""The run log and the health rows built on it.

The point of the log is the run that exits 0 and does nothing, so the tests
sit there: counts survive the wrapper, the exit code is passed through
untouched, a job that goes quiet or dry is named, and a healthy week says so.

Run: python3 -m unittest tools.tests.test_run_log -v
"""
import importlib
import io
import contextlib
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))


class RunLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {k: os.environ.get(k) for k in ("BRAINLESS_VAULT", "BRAINLESS_HOST")}
        os.environ["BRAINLESS_VAULT"] = self.tmp.name
        os.environ["BRAINLESS_HOST"] = "testhost"
        import run_log
        self.rl = importlib.reload(run_log)

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def exec(self, code, job="job"):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = self.rl.main(["exec", "--job", job, "--", sys.executable, "-c", code])
        return rc, out.getvalue()

    def test_counts_are_recorded_and_output_passes_through(self):
        rc, out = self.exec("print('hello'); print('RUNLOG captures=4 note=x')")
        self.assertEqual(rc, 0)
        self.assertIn("hello", out)
        row = self.rl.load()[-1]
        self.assertEqual((row["job"], row["status"], row["counts"]), ("job", "ok", {"captures": 4, "note": "x"}))
        md = (Path(self.tmp.name) / "_Agent-Context/RUNS-testhost.md").read_text()
        self.assertIn("| job | 1 | 0 |", md)
        self.assertEqual(self.rl.read_rollup(md)[0]["counts"]["captures"], 4)

    def test_exit_code_is_passed_through_and_marks_failure(self):
        rc, _ = self.exec("import sys; print('RUNLOG captures=1'); sys.exit(3)")
        self.assertEqual(rc, 3)
        self.assertEqual(self.rl.load()[-1]["status"], "fail")

    def test_a_job_can_report_a_partial_run(self):
        self.exec("print('RUNLOG digest=1 status=partial')")
        row = self.rl.load()[-1]
        self.assertEqual(row["status"], "partial")
        self.assertNotIn("status", row["counts"])

    def test_keys_may_carry_digits(self):
        # hit5 used to fail the pattern, and the lint's earlier line was kept.
        self.exec("print('RUNLOG broken=3'); print('RUNLOG compile_rc=1 hit5=94 status=partial')")
        row = self.rl.load()[-1]
        self.assertEqual((row["status"], row["counts"]), ("partial", {"compile_rc": 1, "hit5": 94}))

    def test_rollup_sums_counts_per_day_and_job(self):
        now = datetime(2026, 9, 22, 23, 0)
        for h, n in ((1, 2), (5, 3)):
            self.rl.record("nightly", "ok", 1.0, {"captures": n}, now=now.replace(hour=h))
        roll = self.rl.rollup(self.rl.load(), now=now)
        self.assertEqual((roll[0]["runs"], roll[0]["counts"]["captures"], roll[0]["last"]), (2, 5, "05:00"))

    def test_job_name_comes_from_the_script(self):
        self.assertEqual(self.rl.job_name(["python3", "tools/nightly_processor.py", "--x"]), "nightly_processor")


class HealthFindings(unittest.TestCase):
    """health_check.run_findings over synthetic rollups."""

    @classmethod
    def setUpClass(cls):
        import health_check
        cls.hc = health_check

    def day(self, n, job, runs=1, status="ok", last="23:00", **counts):
        d = (self.now - timedelta(days=n)).strftime("%Y-%m-%d")
        return {"day": d, "job": job, "runs": runs, "fails": status == "fail", "last": last,
                "status": status, "counts": counts}

    def setUp(self):
        self.now = datetime(2026, 9, 22, 12, 0)

    def texts(self, roll):
        return [msg for _, msg in self.hc.run_findings(roll, now=self.now)]

    def test_a_healthy_week_has_no_findings(self):
        roll = [self.day(n, "nightly_processor", captures=3) for n in range(1, 8)]
        self.assertEqual(self.texts(roll), [])

    def test_dry_capture_is_named(self):
        roll = [self.day(n, "nightly_processor", captures=0) for n in range(1, 6)]
        self.assertTrue(any("nightly_processor" in m and "captures" in m for m in self.texts(roll)))

    def test_a_short_history_is_not_called_dry(self):
        roll = [self.day(1, "nightly_processor", captures=0)]
        self.assertEqual(self.texts(roll), [])

    def test_a_quiet_daily_job_and_a_failed_run_are_named(self):
        roll = [self.day(n, "lint_wiki") for n in range(6, 10)]
        roll += [self.day(1, "dashboard", status="fail"), self.day(2, "dashboard")]
        found = self.texts(roll)
        self.assertTrue(any("lint_wiki" in m for m in found))
        self.assertTrue(any("dashboard" in m for m in found))

    def test_a_partial_run_is_named(self):
        roll = [self.day(1, "nightly_compile", status="partial"), self.day(2, "nightly_compile")]
        self.assertTrue(any("nightly_compile" in m for m in self.texts(roll)))

    def test_a_weekly_job_is_not_quiet_after_three_days(self):
        roll = [self.day(n, "wiki_prune") for n in (3, 10)]
        self.assertEqual(self.texts(roll), [])


if __name__ == "__main__":
    unittest.main()
