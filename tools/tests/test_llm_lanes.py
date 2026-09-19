"""Lane routing rules, and the one that protects privacy.

The interesting failure is silent. If a lane pinned to the local model falls
back to a cloud provider when the local one times out, nothing breaks, nothing
logs red, and the exact content that was pinned local leaves the machine. So
the direction of the fallback is tested from the leaking side, hard.
"""
from pathlib import Path
import os
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import llm  # noqa: E402


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
