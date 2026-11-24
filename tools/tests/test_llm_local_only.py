"""Fail-closed routing: a local-only lane never reaches a cloud provider.

The leak this guards against is silent. Before brainless.toml, the local lanes
were set only in .agents/scripts/env.sh, so a job or an agent that ran a tool
without sourcing it sent every one of them to claude-cli, and nothing logged.
Each test here is written from the leaking side: the dispatcher must not be
called at all.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config
import llm
from test_llm_lanes import Env

LOCAL = "http://127.0.0.1:8080/v1"


class LocalOnlyRouting(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        for name, attr in (("llm_log", "LOG_FILE"), ("llm_status", "STATUS_FILE"),
                           ("llm_costs.jsonl", "COST_FILE"), ("llm_budget.json", "BUDGET_FILE"),
                           ("egress.jsonl", "EGRESS_FILE")):
            p = mock.patch.object(llm, attr, str(d / name))
            p.start()
            self.addCleanup(p.stop)
        self.calls = []
        p = mock.patch.object(llm, "_dispatch",
                              side_effect=lambda provider, *a, **k: self.calls.append(provider) or None)
        p.start()
        self.addCleanup(p.stop)

    def test_cloud_provider_is_refused_and_nothing_is_dispatched(self):
        with Env(BRAINLESS_LLM_LOCAL_ONLY="ask", BRAINLESS_LLM_PROVIDER_ASK="claude-cli"):
            self.assertIsNone(llm.run_prompt("secret note", lane="ask"))
        self.assertEqual(self.calls, [])

    def test_unset_provider_falls_to_default_and_is_refused(self):
        # The exact env.sh-not-sourced case: the lane variable is simply missing.
        with Env(BRAINLESS_LLM_LOCAL_ONLY="ask", BRAINLESS_LLM_PROVIDER_ASK=None,
                 BRAINLESS_LLM_PROVIDER=None):
            self.assertIsNone(llm.run_prompt("secret note", lane="ask"))
        self.assertEqual(self.calls, [])

    def test_local_provider_runs(self):
        with Env(BRAINLESS_LLM_LOCAL_ONLY="ask", BRAINLESS_LLM_PROVIDER_ASK="openai-compatible",
                 BRAINLESS_LLM_BASE_URL=LOCAL, BRAINLESS_LLM_FALLBACK_ASK=None, BRAINLESS_LLM_FALLBACK=None):
            llm.run_prompt("note", lane="ask")
        self.assertEqual(self.calls, ["openai-compatible"])

    def test_remote_openai_compatible_counts_as_cloud(self):
        with Env(BRAINLESS_LLM_LOCAL_ONLY="ask", BRAINLESS_LLM_PROVIDER_ASK="openai-compatible",
                 BRAINLESS_LLM_BASE_URL="https://api.example.com/v1"):
            self.assertIsNone(llm.run_prompt("note", lane="ask"))
        self.assertEqual(self.calls, [])

    def test_cloud_fallback_blocked_even_with_the_opt_in(self):
        with Env(BRAINLESS_LLM_LOCAL_ONLY="ask", BRAINLESS_LLM_PROVIDER_ASK="openai-compatible",
                 BRAINLESS_LLM_BASE_URL=LOCAL, BRAINLESS_LLM_FALLBACK_ASK="claude-cli",
                 BRAINLESS_LLM_ALLOW_CLOUD_FALLBACK="1"):
            llm.run_prompt("note", lane="ask")
        self.assertEqual(self.calls, ["openai-compatible"])

    def test_web_call_on_a_local_only_lane_is_refused(self):
        with Env(BRAINLESS_LLM_LOCAL_ONLY="ask", BRAINLESS_LLM_PROVIDER_ASK="openai-compatible",
                 BRAINLESS_LLM_BASE_URL=LOCAL):
            self.assertIsNone(llm.run_prompt("note", lane="ask", web=True))
        self.assertEqual(self.calls, [])

    def test_lane_outside_the_list_is_untouched(self):
        with Env(BRAINLESS_LLM_LOCAL_ONLY="ask", BRAINLESS_LLM_PROVIDER_COMPILE="claude-cli",
                 BRAINLESS_LLM_FALLBACK_COMPILE=None, BRAINLESS_LLM_FALLBACK=None):
            llm.run_prompt("doc", lane="compile")
        self.assertEqual(self.calls, ["claude-cli"])


class TomlLocalOnly(unittest.TestCase):
    def test_list_becomes_a_comma_variable(self):
        env = config.to_env({"llm": {"local_only": ["ask", "capture-note"],
                                     "lanes": {"ask": {"provider": "openai-compatible"}}}})
        self.assertEqual(env["BRAINLESS_LLM_LOCAL_ONLY"], "ask,capture-note")
        self.assertEqual(env["BRAINLESS_LLM_PROVIDER_ASK"], "openai-compatible")

    def test_vault_config_pins_the_seven_lanes_local(self):
        # The real brainless.toml: every local-only lane is a known lane with a
        # local provider block, so routing holds without env.sh.
        try:
            import tomllib
        except ModuleNotFoundError:
            self.skipTest("tomllib needs Python 3.11+")
        path = Path(__file__).resolve().parents[2] / "brainless.toml"
        if not path.exists():
            self.skipTest("no vault brainless.toml (public mirror)")
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        local_only = data["llm"]["local_only"]
        self.assertTrue(local_only)
        for lane in local_only:
            self.assertIn(lane, llm.LANES)
            self.assertEqual(data["llm"]["lanes"][lane]["provider"], "openai-compatible")
        host = data["llm"]["base_url"]
        self.assertTrue(host.startswith(("http://127.0.0.1", "http://localhost")))

    def test_source_tells_env_from_file(self):
        with Env(BRAINLESS_ZZ_PROBE="x"):
            self.assertEqual(config.source("BRAINLESS_ZZ_PROBE"), "env")
        self.assertEqual(config.source("BRAINLESS_ZZ_PROBE"), "default")


if __name__ == "__main__":
    unittest.main()
