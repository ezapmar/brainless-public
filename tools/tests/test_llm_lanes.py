"""Lane routing rules, and the one that protects privacy.

The interesting failure is silent. If a lane pinned to the local model falls
back to a cloud provider when the local one times out, nothing breaks, nothing
logs red, and the exact content that was pinned local leaves the machine. So
the direction of the fallback is tested from the leaking side, hard.
"""
from pathlib import Path
import os
import unittest

ROOT = Path(__file__).resolve().parents[2]
import llm


class Env:
    """Set env vars for one test and put the environment back afterwards."""

    def __init__(self, **kv):
        self.kv = kv
        self.old = {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.old[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return self

    def __exit__(self, *exc):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestProviderResolution(unittest.TestCase):
    def test_default_is_claude_cli(self):
        with Env(BRAINLESS_LLM_PROVIDER=None, BRAINLESS_LLM_PROVIDER_COMPILE=None):
            self.assertEqual(llm.resolve_provider("compile"), "claude-cli")

    def test_global_applies_to_every_lane(self):
        with Env(BRAINLESS_LLM_PROVIDER="goose"):
            self.assertEqual(llm.resolve_provider("compile"), "goose")
            self.assertEqual(llm.resolve_provider(None), "goose")

    def test_lane_beats_global(self):
        with Env(BRAINLESS_LLM_PROVIDER="claude-cli",
                 BRAINLESS_LLM_PROVIDER_NOTE_CLASSIFY="goose"):
            self.assertEqual(llm.resolve_provider("note-classify"), "goose")
            self.assertEqual(llm.resolve_provider("compile"), "claude-cli")

    def test_lane_name_maps_to_env_name(self):
        # capture-note -> BRAINLESS_LLM_PROVIDER_CAPTURE_NOTE, dashes to underscores.
        with Env(BRAINLESS_LLM_PROVIDER_CAPTURE_NOTE="goose"):
            self.assertEqual(llm.resolve_provider("capture-note"), "goose")


class TestFallbackDirection(unittest.TestCase):
    def test_cloud_may_degrade_to_local(self):
        # Resilience: Claude is down, the worker keeps working.
        with Env(BRAINLESS_LLM_FALLBACK="goose"):
            self.assertEqual(llm._resolve_fallback("compile", "claude-cli"), "goose")

    def test_local_never_escalates_to_cloud(self):
        # Privacy: a lane pinned local stays local, even when it fails.
        with Env(BRAINLESS_LLM_FALLBACK="claude-cli",
                 BRAINLESS_LLM_ALLOW_CLOUD_FALLBACK=None):
            self.assertIsNone(llm._resolve_fallback("note-classify", "goose"))

    def test_local_to_cloud_needs_an_explicit_opt_in(self):
        with Env(BRAINLESS_LLM_FALLBACK="claude-cli",
                 BRAINLESS_LLM_ALLOW_CLOUD_FALLBACK="1"):
            self.assertEqual(llm._resolve_fallback("note-classify", "goose"), "claude-cli")

    def test_lane_can_switch_its_fallback_off(self):
        with Env(BRAINLESS_LLM_FALLBACK="goose",
                 BRAINLESS_LLM_FALLBACK_COMPILE="none"):
            self.assertIsNone(llm._resolve_fallback("compile", "claude-cli"))

    def test_no_fallback_configured_means_none(self):
        with Env(BRAINLESS_LLM_FALLBACK=None, BRAINLESS_LLM_FALLBACK_COMPILE=None):
            self.assertIsNone(llm._resolve_fallback("compile", "claude-cli"))

    def test_fallback_equal_to_primary_is_dropped(self):
        with Env(BRAINLESS_LLM_FALLBACK="claude-cli"):
            self.assertIsNone(llm._resolve_fallback("compile", "claude-cli"))


class TestModelResolution(unittest.TestCase):
    def test_lane_model_beats_global_model(self):
        with Env(BRAINLESS_GOOSE_MODEL="small", BRAINLESS_GOOSE_MODEL_COMPILE="big"):
            self.assertEqual(llm._model_env("BRAINLESS_GOOSE_MODEL", "compile", ""), "big")
            self.assertEqual(llm._model_env("BRAINLESS_GOOSE_MODEL", "nightly", ""), "small")

    def test_empty_variable_means_the_cli_default(self):
        # Presence, not truth: an explicitly empty value must not fall back to
        # the pinned model, or "use whatever the CLI picks" becomes unsayable.
        with Env(BRAINLESS_CLAUDE_MODEL=""):
            self.assertEqual(
                llm._model_env("BRAINLESS_CLAUDE_MODEL", "compile", "claude-opus-4-8"), "")


class TestTimeout(unittest.TestCase):
    def test_local_timeout_is_stretched(self):
        with Env(BRAINLESS_LOCAL_TIMEOUT_FACTOR="3"):
            self.assertEqual(llm._local_timeout(120), 360)

    def test_a_broken_factor_does_not_crash_the_call(self):
        with Env(BRAINLESS_LOCAL_TIMEOUT_FACTOR="banana"):
            self.assertEqual(llm._local_timeout(120), 360)

    def test_factor_below_one_never_shortens(self):
        with Env(BRAINLESS_LOCAL_TIMEOUT_FACTOR="0.1"):
            self.assertEqual(llm._local_timeout(120), 120)


class TestBudget(unittest.TestCase):
    """The budget guard: spend is summed from api-billed rows of this period only,
    and only the anthropic provider is rerouted, to a provider that does not
    spend the budget."""

    def setUp(self):
        import tempfile
        from unittest import mock
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.costs = Path(self.tmp.name) / "llm_costs.jsonl"
        for patcher in (mock.patch.object(llm, "COST_FILE", str(self.costs)),
                        mock.patch.object(llm, "BUDGET_FILE", str(Path(self.tmp.name) / "llm_budget.json")),
                        mock.patch.object(llm, "LOG_FILE", str(Path(self.tmp.name) / "llm_log"))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def rows(self, *specs):
        import json
        from datetime import datetime, timedelta
        now = datetime.now()
        lines = []
        for days_ago, lane, usd, billed in specs:
            ts = (now - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
            lines.append(json.dumps({"ts": ts, "lane": lane, "provider": "anthropic" if billed == "api" else "claude-cli",
                                     "model": "m", "in": 1, "out": 1, "cw": 0, "cr": 0, "usd": usd, "billed": billed}))
        self.costs.write_text("\n".join(lines) + "\n")

    def test_no_budget_means_no_state_and_no_reroute(self):
        with Env(BRAINLESS_API_BUDGET_USD=None):
            self.assertIsNone(llm.budget_state())
            self.assertEqual(llm.budget_reroute("compile", "anthropic"), ("anthropic", None))

    def test_only_api_rows_of_this_period_count(self):
        from datetime import datetime
        self.rows((0, "compile", 10.0, "api"), (0, "nightly", 5.0, "plan"), (0, "x", 1.0, "local"),
                  (40, "compile", 50.0, "api"))
        with Env(BRAINLESS_API_BUDGET_USD="100", BRAINLESS_API_BUDGET_RESET_DAY=str(datetime.now().day)):
            state = llm.budget_state()
        self.assertEqual(state["spent"], 10.0)
        self.assertEqual(state["level"], "ok")

    def test_levels_and_reroute(self):
        from datetime import datetime
        day = str(datetime.now().day)
        self.rows((0, "compile", 85.0, "api"))
        with Env(BRAINLESS_API_BUDGET_USD="100", BRAINLESS_API_BUDGET_RESET_DAY=day,
                 BRAINLESS_LLM_FALLBACK=None):
            self.assertEqual(llm.budget_state()["level"], "warn")
            self.assertEqual(llm.budget_reroute("compile", "anthropic"), ("anthropic", None))
        self.rows((0, "compile", 91.0, "api"))
        with Env(BRAINLESS_API_BUDGET_USD="100", BRAINLESS_API_BUDGET_RESET_DAY=day,
                 BRAINLESS_LLM_FALLBACK=None):
            self.assertEqual(llm.budget_state()["level"], "stop")
            target, why = llm.budget_reroute("compile", "anthropic")
            self.assertEqual(target, "claude-cli")
            self.assertIn("stop ratio", why)
            # a lane's own cloud fallback is honoured; an anthropic or a local
            # one is not (local is for failures, not for a billing overrun)
            with Env(BRAINLESS_LLM_FALLBACK_COMPILE="grok"):
                self.assertEqual(llm.budget_reroute("compile", "anthropic")[0], "grok")
            with Env(BRAINLESS_LLM_FALLBACK_COMPILE="goose"):
                self.assertEqual(llm.budget_reroute("compile", "anthropic")[0], "claude-cli")
            with Env(BRAINLESS_LLM_FALLBACK="anthropic"):
                self.assertEqual(llm.budget_reroute("compile", "anthropic")[0], "claude-cli")
            # the CLI and local providers never move
            self.assertEqual(llm.budget_reroute("compile", "claude-cli"), ("claude-cli", None))
            self.assertEqual(llm.budget_reroute("x", "goose"), ("goose", None))

    def test_period_start_handles_the_day_before_reset(self):
        from datetime import datetime
        with Env(BRAINLESS_API_BUDGET_RESET_DAY="15"):
            self.assertEqual(llm.budget_period_start(datetime(2026, 10, 14)), datetime(2026, 9, 15))
            self.assertEqual(llm.budget_period_start(datetime(2026, 10, 15)), datetime(2026, 10, 15))
            self.assertEqual(llm.budget_period_start(datetime(2026, 1, 3)), datetime(2025, 12, 15))

    def test_usage_report_sums_per_lane(self):
        from datetime import datetime
        self.rows((0, "compile", 1.5, "api"), (0, "compile", 0.5, "api"), (0, "nightly", 2.0, "plan"))
        with Env(BRAINLESS_API_BUDGET_USD="100", BRAINLESS_API_BUDGET_RESET_DAY=str(datetime.now().day)):
            out = llm.usage_report()
        self.assertIn("3 priced calls", out)
        self.assertIn("2.00 of 100 USD", out)
        line = next(ln for ln in out.splitlines() if ln.startswith("compile"))
        self.assertIn("2.00", line)
        self.assertIn("total", out)


class TestLaneTable(unittest.TestCase):
    def test_every_call_site_uses_a_known_lane(self):
        """A typo in lane="..." would silently route to the global provider."""
        import re
        used = set()
        for path in list((ROOT / "tools").rglob("*.py")) + \
                list((ROOT / ".agents" / "scripts").rglob("*.py")):
            if "tests" in str(path):
                continue
            used |= set(re.findall(r'run_prompt\([^)]*lane="([a-z-]+)"', path.read_text(),
                                   re.S))
        self.assertTrue(used, "no lane-tagged call sites found; did the grep break?")
        self.assertEqual(used - set(llm.LANES), set())


if __name__ == "__main__":
    unittest.main()
