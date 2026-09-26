"""paths.py and config.py: where the vault is, what its folders are called, and
how brainless.toml reaches the environment without overriding it.

The two promises worth guarding: a vault with no brainless.toml behaves exactly
as before (full-layout defaults, env untouched), and a key in the environment
always beats the file, so an operator can override one setting for one run.
Secrets are tested on the file backend only; it is the one every machine has.
"""
from pathlib import Path
import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import config  # noqa: E402
import paths  # noqa: E402

FOLDER_VARS = [f"BRAINLESS_FOLDER_{k.upper()}" for k in paths.FOLDER_KEYS] + ["BRAINLESS_SOURCES"]


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if k not in FOLDER_VARS}
    env.update(extra)
    return mock.patch.dict(os.environ, env, clear=True)


class TestVaultRoot(unittest.TestCase):
    def test_env_wins(self):
        with clean_env(BRAINLESS_VAULT="~/somewhere"):
            self.assertEqual(paths.vault_root(), os.path.expanduser("~/somewhere"))

    def test_fallback_is_the_checkout(self):
        env = {k: v for k, v in os.environ.items() if k != "BRAINLESS_VAULT"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(paths.vault_root(), str(ROOT))


class TestFolders(unittest.TestCase):
    def test_defaults_are_the_full_layout(self):
        with clean_env():
            self.assertEqual(paths.folder("inbox"), "Inbox")
            self.assertEqual(paths.folder("daily"), "Thinking/Daily")
            self.assertEqual(paths.folder("decisions"), "Thinking/Decisions")
            self.assertEqual(paths.extra_sources(), ())

    def test_renamed_thinking_moves_its_children(self):
        with clean_env(BRAINLESS_FOLDER_THINKING="Düşünce"):
            self.assertEqual(paths.folder("beliefs"), "Düşünce/Beliefs")

    def test_child_set_on_its_own(self):
        with clean_env(BRAINLESS_FOLDER_THINKING="Düşünce", BRAINLESS_FOLDER_DAILY="Notlar"):
            self.assertEqual(paths.folder("daily"), "Notlar")

    def test_escape_is_refused(self):
        for bad in ("../elsewhere", "/etc", "a/../../b"):
            with clean_env(BRAINLESS_FOLDER_INBOX=bad):
                with self.assertRaises(ValueError):
                    paths.folder("inbox")

    def test_nfc(self):
        nfd = "Kütüphane"
        with clean_env(BRAINLESS_FOLDER_LIBRARY=nfd):
            self.assertEqual(paths.folder("library"), "Kütüphane")

    def test_skeleton_localised_with_english_fallback(self):
        tr = paths.skeleton("tr")
        self.assertEqual(tr["inbox"], "Gelen")
        self.assertEqual(tr["daily"], "Notlar")
        self.assertEqual(paths.skeleton("xx")["daily"], "Notes")
        self.assertEqual(set(tr), set(paths.FOLDER_KEYS))


class TestConfig(unittest.TestCase):
    TOML = """
profile = "lite"
[owner]
name = "Ada"
lang = "tr"
[folders]
inbox = "Gelen"
daily = "Notlar"
sources = ["Arşiv", "Projeler"]
[llm]
provider = "openai-compatible"
base_url = "http://localhost:11434/v1"
model = "qwen3:8b"
api_key = "sk-must-not-leak"
[llm.lanes.capture-note]
provider = "anthropic"
model = "claude-haiku-4-5"
[schedule]
enabled = true
[env]
BRAINLESS_LLM_MAX_CHARS = "12000"
BRAINLESS_LLM_API_KEY = "sk-nope"
"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        Path(self.tmp.name, "brainless.toml").write_text(self.TOML, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def env(self):
        with mock.patch("sys.stderr"):
            return config.to_env(config.load(self.tmp.name))

    def test_mapping(self):
        e = self.env()
        self.assertEqual(e["BRAINLESS_PROFILE"], "lite")
        self.assertEqual(e["BRAINLESS_OWNER_NAME"], "Ada")
        self.assertEqual(e["BRAINLESS_OUTPUT_LANG"], "tr")
        self.assertEqual(e["BRAINLESS_FOLDER_DAILY"], "Notlar")
        self.assertEqual(e["BRAINLESS_SOURCES"], "Arşiv,Projeler")
        self.assertEqual(e["BRAINLESS_LLM_BASE_URL"], "http://localhost:11434/v1")
        self.assertEqual(e["BRAINLESS_LLM_PROVIDER_CAPTURE_NOTE"], "anthropic")
        self.assertEqual(e["BRAINLESS_LLM_MODEL_CAPTURE_NOTE"], "claude-haiku-4-5")
        self.assertEqual(e["BRAINLESS_SCHEDULE"], "1")
        self.assertEqual(e["BRAINLESS_LLM_MAX_CHARS"], "12000")

    def test_secrets_never_travel_through_the_file(self):
        e = self.env()
        self.assertNotIn("BRAINLESS_LLM_API_KEY", e)
        self.assertNotIn("sk-must-not-leak", "".join(e.values()))

    def test_env_beats_file(self):
        with clean_env(BRAINLESS_OWNER_NAME="Env", BRAINLESS_VAULT=self.tmp.name), mock.patch("sys.stderr"):
            os.environ.pop("BRAINLESS_OUTPUT_LANG", None)
            config.apply(force=True)
            self.assertEqual(os.environ["BRAINLESS_OWNER_NAME"], "Env")
            self.assertEqual(os.environ["BRAINLESS_OUTPUT_LANG"], "tr")
            self.assertEqual(paths.folder("inbox"), "Gelen")

    def test_absent_file_sets_nothing(self):
        with tempfile.TemporaryDirectory() as empty:
            self.assertEqual(config.to_env(config.load(empty)), {})


class TestFileSecrets(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.dict(os.environ, {"BRAINLESS_SECRETS_BACKEND": "file",
                                                  "XDG_CONFIG_HOME": self.tmp.name})
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_round_trip_and_mode(self):
        self.assertIsNone(config.get_secret("llm_api_key"))
        config.set_secret("llm_api_key", "sk-1=2")
        self.assertEqual(config.get_secret("llm_api_key"), "sk-1=2")
        f = Path(self.tmp.name, "brainless", "secrets.env")
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        self.assertTrue(config.delete_secret("llm_api_key"))
        self.assertIsNone(config.get_secret("llm_api_key"))

    def test_bad_input_refused(self):
        with self.assertRaises(ValueError):
            config.set_secret("../x", "v")
        with self.assertRaises(ValueError):
            config.set_secret("ok", "two\nlines")


if __name__ == "__main__":
    unittest.main()
