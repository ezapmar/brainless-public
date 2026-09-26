"""The lite profile's queue and its one scheduler entry.

The rules worth pinning are the ones a laptop exercises: a missed night runs at
the next wake, yesterday's run waits for this evening, an offline tick runs the
offline jobs and keeps the rest, a failing job backs off and finally stops, a
crash mid-run does not strand a job, and two ticks never run at once. The
scheduler files are checked for what launchd and systemd actually read.
"""
from datetime import datetime, timedelta
from pathlib import Path
import os
import plistlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import jobqueue as jq  # noqa: E402
import schedule  # noqa: E402

EVENING = datetime(2026, 9, 26, 22, 0)
MORNING = datetime(2026, 9, 26, 8, 0)


class TestDue(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"BRAINLESS_DAILY_HOUR": "21"})
        p.start()
        self.addCleanup(p.stop)

    def ts(self, dt):
        return dt.timestamp()

    def test_daily_never_run(self):
        self.assertTrue(jq.is_due("daily", None, EVENING))
        self.assertFalse(jq.is_due("daily", None, MORNING))

    def test_daily_ran_today(self):
        self.assertFalse(jq.is_due("daily", self.ts(EVENING - timedelta(hours=1)), EVENING))

    def test_daily_ran_yesterday_waits_for_evening(self):
        yesterday = self.ts(EVENING - timedelta(days=1))
        self.assertFalse(jq.is_due("daily", yesterday, MORNING))
        self.assertTrue(jq.is_due("daily", yesterday, EVENING))

    def test_daily_missed_a_whole_day_runs_at_wake(self):
        self.assertTrue(jq.is_due("daily", self.ts(EVENING - timedelta(days=2)), MORNING))

    def test_hourly_and_weekly(self):
        self.assertTrue(jq.is_due("hourly", self.ts(EVENING - timedelta(minutes=61)), EVENING))
        self.assertFalse(jq.is_due("hourly", self.ts(EVENING - timedelta(minutes=30)), EVENING))
        self.assertFalse(jq.is_due("weekly", self.ts(EVENING - timedelta(days=6)), EVENING))
        self.assertTrue(jq.is_due("weekly", self.ts(EVENING - timedelta(days=7)), EVENING))


class VaultTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = {k: v for k, v in os.environ.items() if not k.startswith("BRAINLESS_FOLDER_")}
        env.update(BRAINLESS_VAULT=self.tmp.name, BRAINLESS_DAILY_HOUR="21",
                   BRAINLESS_OUTPUT_LANG="en")
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)
        self.db = jq.connect()
        self.addCleanup(self.db.close)


class TestQueue(VaultTest):
    def test_enqueue_dedupes_waiting_jobs(self):
        self.assertIsNotNone(jq.enqueue(self.db, "compile"))
        self.assertIsNone(jq.enqueue(self.db, "compile"))
        with self.assertRaises(KeyError):
            jq.enqueue(self.db, "rm-rf")

    def test_offline_claims_only_offline_jobs(self):
        jq.enqueue(self.db, "compile")
        jq.enqueue(self.db, "lint")
        row = jq.claim(self.db, can_net=False, can_llm=False)
        self.assertEqual(row["kind"], "lint")
        self.assertIsNone(jq.claim(self.db, can_net=False, can_llm=False))
        self.assertEqual(jq.claim(self.db, can_net=True, can_llm=True)["kind"], "compile")

    def test_backoff_then_failed(self):
        jq.enqueue(self.db, "compile")
        now = 1_000_000.0
        for attempt, wait in enumerate(jq.BACKOFF, start=1):
            row = jq.claim(self.db, True, True, now=now)
            self.assertIsNotNone(row, f"attempt {attempt}")
            jq.finish(self.db, row, False, "boom", now=now)
            self.assertIsNone(jq.claim(self.db, True, True, now=now + wait - 1))
            now += wait
        row = jq.claim(self.db, True, True, now=now)
        jq.finish(self.db, row, False, "boom", now=now)
        state = self.db.execute("SELECT state, last_error FROM jobs").fetchone()
        self.assertEqual(tuple(state), ("failed", "boom"))

    def test_success_records_last_run(self):
        jq.enqueue(self.db, "compile")
        jq.finish(self.db, jq.claim(self.db, True, True), True, now=123.0)
        self.assertEqual(self.db.execute("SELECT last_run FROM periodic WHERE kind='compile'").fetchone()[0], 123.0)

    def test_stale_running_goes_back(self):
        jq.enqueue(self.db, "compile", now=0)
        jq.claim(self.db, True, True, now=10)
        self.assertEqual(jq.requeue_stale(self.db, now=10 + jq.STALE_RUNNING + 1), 1)
        self.assertIsNotNone(jq.claim(self.db, True, True, now=10 + jq.STALE_RUNNING + 2))


class TestTick(VaultTest):
    def run_tick(self, net, ready, runner, **kw):
        with mock.patch.object(jq, "online", return_value=net), \
             mock.patch.object(jq, "llm_ready", return_value=ready), \
             mock.patch("builtins.print"):
            return jq.tick(runner=runner, now=EVENING, **kw)

    def test_first_evening_runs_everything_in_order(self):
        seen = []
        res = self.run_tick(True, True, lambda row: (seen.append(row["kind"]), (True, ""))[1])
        self.assertEqual(seen, list(jq.JOBS))
        self.assertEqual(res["waiting"], 0)

    def test_offline_keeps_model_jobs(self):
        res = self.run_tick(False, False, lambda row: (True, ""))
        self.assertEqual(sorted(res["done"]), ["convert", "lint", "prune"])
        self.assertEqual(res["waiting"], 2)

    def test_dry_run_changes_nothing(self):
        self.run_tick(True, True, lambda row: (True, ""), dry_run=True)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)

    def test_second_tick_is_locked_out(self):
        with jq.Lock() as held:
            self.assertTrue(held)
            with mock.patch("builtins.print"):
                self.assertEqual(jq.tick(), {"skipped": True})


class TestCapture(VaultTest):
    def test_note_goes_to_the_daily_folder(self):
        os.environ["BRAINLESS_FOLDER_DAILY"] = "Notlar"
        path = jq.capture_text("one thought", now=EVENING)
        self.assertEqual(Path(path).parent.name, "Notlar")
        text = Path(path).read_text()
        self.assertTrue(text.startswith("# one thought\n"))
        self.assertIn("brainless add, 2026-09-26 22:00", text)

    def test_long_note_keeps_its_body(self):
        body = "First line\nsecond line"
        text = Path(jq.capture_text(body, now=EVENING)).read_text()
        self.assertIn("# First line\n\nFirst line\nsecond line", text)

    def test_empty_note_refused(self):
        with self.assertRaises(ValueError):
            jq.capture_text("  \n ")

    def test_file_is_copied_and_convert_queued(self):
        src = Path(self.tmp.name) / "report.pdf"
        src.write_bytes(b"%PDF")
        dst = jq.capture_file(str(src), db=self.db)
        self.assertEqual(Path(dst).parent.name, "Inbox")
        self.assertTrue(src.exists())
        self.assertEqual(self.db.execute("SELECT kind FROM jobs").fetchone()[0], "convert")
        self.assertTrue(jq.capture_file(str(src), db=self.db).endswith("report-2.pdf"))


class TestSchedule(unittest.TestCase):
    def test_plist_is_valid_and_runs_tick(self):
        with mock.patch.object(schedule, "job_path", return_value="/usr/bin:/bin"):
            data = plistlib.loads(schedule.launchd_plist().encode())
        self.assertEqual(data["Label"], schedule.LABEL)
        self.assertEqual(data["ProgramArguments"][-2:], [str(ROOT / "tools" / "jobqueue.py"), "tick"])
        self.assertEqual(data["StartInterval"], 900)
        self.assertTrue(data["RunAtLoad"])
        self.assertIn("BRAINLESS_VAULT", data["EnvironmentVariables"])

    def test_systemd_timer_catches_up(self):
        with mock.patch.object(schedule, "job_path", return_value="/usr/bin:/bin"):
            units = schedule.systemd_units()
        timer = units[f"{schedule.UNIT}.timer"]
        self.assertIn("Persistent=true", timer)
        self.assertIn("OnCalendar=*:0/15", timer)
        self.assertIn("jobqueue.py\" \"tick\"", units[f"{schedule.UNIT}.service"])

    def test_install_refused_outside_lite(self):
        with mock.patch.dict(os.environ, {"BRAINLESS_PROFILE": "full"}), \
             mock.patch.object(schedule, "_run") as run, mock.patch("sys.stderr"):
            self.assertEqual(schedule.install(), 2)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
