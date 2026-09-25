"""The worker's LLM canary reaches the Mac's HEALTH.md.

What must hold: the worker's llm_status line is mirrored with the worker's own
mtime, a failed or empty fetch leaves the previous mirror alone, an auth line
in the mirror turns the worker row red while the Mac's own row stays green,
and a machine without a mirror shows no worker row at all.

Run: python3 -B -m unittest tools.tests.test_worker_llm_canary -v
"""
import importlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

AUTH = "2026-09-25 02:10:00\tauth\t[compile] Failed to authenticate: OAuth session expired\n"
OK = "2026-09-25 21:00:00\tok\t[nightly] \n"


class VaultCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.prev = os.environ.get("BRAINLESS_VAULT")
        os.environ["BRAINLESS_VAULT"] = self.tmp.name
        self.state = Path(self.tmp.name, ".agents", "state")
        self.state.mkdir(parents=True)

    def tearDown(self):
        if self.prev is None:
            os.environ.pop("BRAINLESS_VAULT", None)
        else:
            os.environ["BRAINLESS_VAULT"] = self.prev


class Fetch(VaultCase):
    def setUp(self):
        super().setUp()
        import worker_reach
        self.wr = importlib.reload(worker_reach)

    def test_parse_keeps_worker_mtime_and_first_line(self):
        m = self.wr.parse_llm_status("1790000000\n" + AUTH + "junk\n", 5)
        self.assertEqual(m, {"fetched_at": 5, "mtime": 1790000000, "line": AUTH.strip()})

    def test_parse_rejects_missing_or_garbled_output(self):
        self.assertIsNone(self.wr.parse_llm_status("", 5))
        self.assertIsNone(self.wr.parse_llm_status("1790000000\n", 5))
        self.assertIsNone(self.wr.parse_llm_status("stat: cannot stat\n", 5))

    def test_fetch_writes_mirror(self):
        with patch.object(self.wr, "run", return_value=Mock(returncode=0, stdout="1790000000\n" + AUTH)):
            self.wr.fetch_llm_status("h", 7)
        m = json.loads(Path(self.wr.LLM_MIRROR).read_text())
        self.assertIn("OAuth session expired", m["line"])

    def test_failed_fetch_keeps_previous_mirror(self):
        Path(self.wr.LLM_MIRROR).write_text(json.dumps({"fetched_at": 1, "mtime": 1, "line": OK.strip()}))
        with patch.object(self.wr, "run", return_value=Mock(returncode=255, stdout="")):
            self.assertIsNone(self.wr.fetch_llm_status("h", 7))
        self.assertIn("\tok\t", json.loads(Path(self.wr.LLM_MIRROR).read_text())["line"])

    def test_main_skips_fetch_when_link_is_down(self):
        with patch.object(self.wr, "target", return_value="h"), \
             patch.object(self.wr, "link_down", return_value="ts off"), \
             patch.object(self.wr, "check_pulse") as pulse, \
             patch.object(self.wr, "fetch_llm_status") as fetch, \
             patch.object(self.wr, "notify"):
            self.wr.main()
        fetch.assert_not_called()
        pulse.assert_not_called()

    def test_main_fetches_when_ssh_works_even_if_pulse_is_stale(self):
        with patch.object(self.wr, "target", return_value="h"), \
             patch.object(self.wr, "link_down", return_value=None), \
             patch.object(self.wr, "check_pulse", return_value="stale"), \
             patch.object(self.wr, "fetch_llm_status") as fetch, \
             patch.object(self.wr, "notify"):
            self.wr.main()
        fetch.assert_called_once()


class HealthRow(VaultCase):
    def setUp(self):
        super().setUp()
        import health_check
        self.hc = importlib.reload(health_check)
        self.hc.CHECKS.clear()
        (self.state / "llm_status").write_text(OK)

    def rows(self):
        self.hc.check_llm_auth()
        return self.hc.CHECKS

    def test_no_mirror_means_one_row(self):
        rows = self.rows()
        self.assertEqual([s for _, s, _ in rows], ["OK"])

    def test_worker_auth_is_red_while_mac_is_green(self):
        mirror = {"fetched_at": int(time.time()), "mtime": int(time.time()) - 7200, "line": AUTH.strip()}
        (self.state / "worker_llm_status.json").write_text(json.dumps(mirror))
        rows = self.rows()
        self.assertEqual([s for _, s, _ in rows], ["OK", "RED"])
        label, _, detail = rows[1]
        self.assertIn(self.hc.WORKER, label)
        self.assertIn("OAuth session expired", detail)
        self.assertIn(self.hc.WORKER, detail)
        self.assertIn("claude", detail)

    def test_worker_ok_is_green(self):
        mirror = {"fetched_at": 1, "mtime": int(time.time()) - 60, "line": OK.strip()}
        (self.state / "worker_llm_status.json").write_text(json.dumps(mirror))
        self.assertEqual([s for _, s, _ in self.rows()], ["OK", "OK"])

    def test_broken_mirror_warns(self):
        (self.state / "worker_llm_status.json").write_text("{not json")
        self.assertEqual([s for _, s, _ in self.rows()], ["OK", "WARN"])


if __name__ == "__main__":
    unittest.main()
