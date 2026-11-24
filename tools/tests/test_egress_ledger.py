"""The egress ledger: what left the machine, in counts, never in text.

The failure this guards against is a ledger that looks useful and is dangerous:
one that quotes part of the prompt to make itself readable, or one that forgets
a local call so the weekly report claims everything went to the cloud. Both are
tested from the file on disk, not from the return value.

Run: python3 tools/run_tests.py test_egress_ledger
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import llm
import privacy_report
from test_llm_lanes import Env

LOCAL = "http://127.0.0.1:8080/v1"
SECRET_TEXT = "Ayse 0532 111 22 33, ayse@ornekfirma.com, IBAN TR33 0006 1005 1978 6457 8413 26"


class LedgerFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        for name, attr in (("llm_log", "LOG_FILE"), ("llm_status", "STATUS_FILE"),
                           ("llm_costs.jsonl", "COST_FILE"), ("llm_budget.json", "BUDGET_FILE"),
                           ("egress.jsonl", "EGRESS_FILE")):
            patcher = mock.patch.object(llm, attr, str(d / name))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.path = d / "egress.jsonl"
        self.seen = []

        def fake_dispatch(provider, prompt, *a, **k):
            self.seen.append((provider, prompt))
            return "answer"

        patcher = mock.patch.object(llm, "_dispatch", side_effect=fake_dispatch)
        patcher.start()
        self.addCleanup(patcher.stop)

    def rows(self, phase=None):
        if not self.path.exists():
            return []
        out = [json.loads(line) for line in
               self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [r for r in out if phase is None or r.get("phase") == phase]

    def start(self):
        starts = self.rows("start")
        self.assertTrue(starts, "a send must leave a row")
        return starts[0]


class RowIsSafe(LedgerFixture):
    def test_the_prompt_never_reaches_the_ledger(self):
        with Env(BRAINLESS_LLM_PROVIDER_COMPILE="claude-cli"):
            llm.run_prompt(SECRET_TEXT, lane="compile")
        raw = self.path.read_text(encoding="utf-8")
        for fragment in ("Ayse", "0532 111 22 33", "ayse@ornekfirma.com", "TR33 0006"):
            self.assertNotIn(fragment, raw, f"prompt text {fragment!r} leaked into the ledger")

    def test_no_source_path_or_hash_is_written(self):
        with Env(BRAINLESS_LLM_PROVIDER_COMPILE="claude-cli"):
            llm.run_prompt("prompt", lane="compile", sources=["Work/Notes/x.md"])
        self.assertEqual(set(self.start()),
                         {"ts", "id", "phase", "lane", "provider", "model",
                          "local", "in", "out", "redactions", "web", "sent"})

    def test_a_refused_row_carries_the_flag_but_no_path(self):
        # The list itself is patched: this test is about the row shape, and the
        # vault's PROFILE.md must not decide whether it passes (a module imported
        # earlier can have read a stubbed vault).
        import cloud_gate
        with mock.patch.object(cloud_gate, "CLOUD_EXCLUDE", ("Personal/Pets",)), \
                Env(BRAINLESS_LLM_PROVIDER_COMPILE="claude-cli"):
            llm.run_prompt("prompt", lane="compile", sources=["Personal/Pets/vet.md"])
        row = self.start()
        self.assertTrue(row["blocked"])
        self.assertFalse(row["sent"])
        for key in ("sources", "excluded", "path"):
            self.assertNotIn(key, row)
        self.assertNotIn("Personal/Pets/vet.md", self.path.read_text(encoding="utf-8"))

    def test_a_redacted_cloud_call_records_the_counts(self):
        with Env(BRAINLESS_LLM_PROVIDER_COMPILE="claude-cli"):
            llm.run_prompt(SECRET_TEXT, lane="compile")
        row = self.start()
        self.assertEqual(row["redactions"], {"email": 1, "phone": 1, "iban": 1})
        self.assertEqual(row["in"], len(SECRET_TEXT))
        provider, sent = self.seen[0]
        self.assertEqual(provider, "claude-cli")
        self.assertNotIn("ayse@ornekfirma.com", sent)

    def test_a_local_call_is_marked_local_and_not_masked(self):
        with Env(BRAINLESS_LLM_PROVIDER_NOTE_CLASSIFY="openai-compatible",
                 BRAINLESS_LLM_BASE_URL=LOCAL):
            llm.run_prompt(SECRET_TEXT, lane="note-classify")
        row = self.start()
        self.assertTrue(row["local"])
        self.assertEqual(row["redactions"], {})
        self.assertEqual(self.seen[0][1], SECRET_TEXT, "a local provider sees the text as it is")

    def test_provider_model_lane_and_web_are_recorded(self):
        with Env(BRAINLESS_LLM_PROVIDER_COMPILE="claude-cli",
                 BRAINLESS_CLAUDE_MODEL_COMPILE="claude-opus-5-5"):
            llm.run_prompt("prompt", lane="compile")
        row = self.start()
        self.assertEqual((row["lane"], row["provider"], row["model"]),
                         ("compile", "claude-cli", "claude-opus-5-5"))
        self.assertFalse(row["web"])
        self.assertTrue(row["sent"])


class Completion(LedgerFixture):
    def test_the_output_length_lands_on_a_matching_end_row(self):
        with Env(BRAINLESS_LLM_PROVIDER_COMPILE="claude-cli"):
            llm.run_prompt("prompt", lane="compile")
        start, end = self.start(), self.rows("end")[0]
        self.assertIsNone(start["out"], "output length is unknown at send time")
        self.assertEqual(end["id"], start["id"])
        self.assertEqual(end["out"], len("answer"))
        self.assertTrue(end["ok"])

    def test_a_failed_call_is_closed_with_ok_false(self):
        with mock.patch.object(llm, "_dispatch", return_value=None), \
                Env(BRAINLESS_LLM_PROVIDER_COMPILE="claude-cli", BRAINLESS_LLM_FALLBACK_COMPILE=None):
            self.assertIsNone(llm.run_prompt("prompt", lane="compile"))
        self.assertFalse(self.rows("end")[0]["ok"])

    def test_a_refused_call_has_no_end_row_and_is_not_sent(self):
        with Env(BRAINLESS_LLM_PROVIDER_ASK="claude-cli", BRAINLESS_LLM_LOCAL_ONLY="ask"):
            self.assertIsNone(llm.run_prompt("prompt", lane="ask"))
        self.assertFalse(self.start()["sent"])
        self.assertEqual(self.rows("end"), [], "nothing was sent, so nothing completed")


class Report(LedgerFixture):
    def test_cloud_characters_local_ratio_and_redactions(self):
        with Env(BRAINLESS_LLM_PROVIDER_COMPILE="claude-cli",
                 BRAINLESS_LLM_PROVIDER_NOTE_CLASSIFY="openai-compatible",
                 BRAINLESS_LLM_BASE_URL=LOCAL):
            llm.run_prompt(SECRET_TEXT, lane="compile")
            llm.run_prompt("short note", lane="note-classify")
        now = llm.datetime.now()
        rows = privacy_report.read_rows(str(self.path), now=now, days=7)
        report = privacy_report.build(rows, days=7, now=now)
        t = report["totals"]
        self.assertEqual(t["calls"], 2)
        self.assertEqual(t["local_calls"], 1)
        self.assertEqual(t["cloud_calls"], 1)
        self.assertAlmostEqual(t["local_ratio"], 0.5)
        self.assertEqual(t["cloud_in_chars"], len(SECRET_TEXT))
        self.assertEqual(t["cloud_out_chars"], len("answer"))
        self.assertEqual(t["redactions"], {"email": 1, "phone": 1, "iban": 1})
        self.assertEqual(report["lanes"]["compile"]["cloud_calls"], 1)
        self.assertEqual(report["lanes"]["note-classify"]["local_ratio"], 1.0)

    def test_the_window_drops_old_rows(self):
        import datetime as dt
        now = dt.datetime(2026, 1, 20, 12, 0, 0)
        old = Path(self.tmp.name) / "old.jsonl"
        old.write_text(json.dumps({"ts": "2026-01-01 10:00:00", "phase": "start",
                                   "lane": "compile", "provider": "claude-cli",
                                   "local": False, "in": 100, "sent": True,
                                   "redactions": {}, "web": False, "id": "1"}) + "\n",
                       encoding="utf-8")
        rows = privacy_report.read_rows(str(old), now=now, days=7)
        report = privacy_report.build(rows, days=7, now=now)
        self.assertEqual(report["totals"]["calls"], 0)
        self.assertIn("no calls in the window", privacy_report.render(report))

    def test_the_report_is_written_outside_git(self):
        with Env(BRAINLESS_LLM_PROVIDER_COMPILE="claude-cli"):
            llm.run_prompt("prompt", lane="compile")
        report = privacy_report.build(privacy_report.read_rows(str(self.path)), days=7)
        out = Path(self.tmp.name) / "logs" / "privacy"
        md, js = privacy_report.write_report(report, str(out))
        self.assertTrue(Path(md).exists() and Path(js).exists())
        self.assertIn("Privacy report", Path(md).read_text(encoding="utf-8"))
        self.assertEqual(privacy_report.OUT_DIR,
                         os.path.join(privacy_report.VAULT, "logs", "privacy"))

    def test_summary_line_is_one_line(self):
        line = privacy_report.summary_line(privacy_report.build([], days=7))
        self.assertEqual(line.count("\n"), 0)
        self.assertIn("chars to the cloud", line)


if __name__ == "__main__":
    unittest.main()
