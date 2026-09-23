"""Worker reachability alarm, without ssh, Tailscale or git.

What must hold: one failed hour stays quiet, the second alerts once, later
failures do not repeat it, recovery after an alert says so once, and the
first failing check is the one reported.

Run: python3 -B -m unittest tools.tests.test_worker_reach -v
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import worker_reach as wr  # noqa: E402


class Step(unittest.TestCase):
    def test_alerts_once_on_second_failure(self):
        s, msg = wr.step({}, "down", 1)
        self.assertEqual((s["fails"], msg), (1, None))
        s, msg = wr.step(s, "down", 2)
        self.assertIn("down", msg)
        s, msg = wr.step(s, "down", 3)
        self.assertEqual((s["fails"], msg, s["alerted"]), (3, None, True))

    def test_recovery_after_alert_is_reported_once(self):
        s, msg = wr.step({"fails": 3, "alerted": True}, None, 4)
        self.assertEqual((s["fails"], s["alerted"]), (0, False))
        self.assertIn("after 3", msg)
        self.assertIsNone(wr.step(s, None, 5)[1])

    def test_single_blip_recovers_silently(self):
        s, _ = wr.step({}, "down", 1)
        self.assertIsNone(wr.step(s, None, 2)[1])


class Diagnose(unittest.TestCase):
    def test_tailscale_reported_before_ssh(self):
        with patch.object(wr, "check_tailscale", return_value="ts off"), \
             patch.object(wr, "check_ssh") as ssh:
            self.assertEqual(wr.diagnose("h", 0), "ts off")
        ssh.assert_not_called()

    def test_pulse_checked_when_ssh_works(self):
        with patch.object(wr, "check_tailscale", return_value=None), \
             patch.object(wr, "check_ssh", return_value=None), \
             patch.object(wr, "check_pulse", return_value="stale"):
            self.assertEqual(wr.diagnose("h", 0), "stale")

    def test_pulse_reads_worker_commits_only(self):
        now = 10 * 3600
        log = f"{now - 60}\tcontext refresh: x\n{now - 3 * 3600}\tvault backup: y ({wr.WORKER})\n"
        fake = unittest.mock.Mock(stdout=log, returncode=0)
        with patch.object(wr, "run", return_value=fake):
            self.assertIn("3.0 h", wr.check_pulse(now))


if __name__ == "__main__":
    unittest.main()
