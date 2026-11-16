"""Google OAuth files live outside the vault (config.google_file, 2026-10-08)."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools")) if str(ROOT / "tools") not in sys.path else None
import config  # noqa: E402


class GoogleFiles(unittest.TestCase):
    def test_default_is_under_the_user_config_dir(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BRAINLESS_GOOGLE_DIR", None)
            self.assertTrue(config.google_dir().endswith(os.path.join(".config", "brainless", "google")))

    def test_env_override_and_legacy_fallback(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as vault:
            with mock.patch.dict(os.environ, {"BRAINLESS_GOOGLE_DIR": home, "BRAINLESS_VAULT": vault}):
                self.assertEqual(config.google_file("token.json"), os.path.join(home, "token.json"))
                legacy = Path(vault) / "tools" / "tasks-sync"
                legacy.mkdir(parents=True)
                (legacy / "token.json").write_text("{}", encoding="utf-8")
                with mock.patch.object(config, "_warn") as warn:
                    self.assertEqual(config.google_file("token.json"), str(legacy / "token.json"))
                warn.assert_called_once()
                Path(home, "token.json").write_text("{}", encoding="utf-8")
                self.assertEqual(config.google_file("token.json"), os.path.join(home, "token.json"))


if __name__ == "__main__":
    unittest.main()
