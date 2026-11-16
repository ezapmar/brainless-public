"""wiki_prune counts the vault's own folder names, not fixed English ones."""
import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]


class PruneFolders(unittest.TestCase):
    def test_renamed_folders_are_followed(self):
        with tempfile.TemporaryDirectory() as vault:
            env = {"BRAINLESS_VAULT": vault, "BRAINLESS_FOLDER_INBOX": "Gelen",
                   "BRAINLESS_FOLDER_THINKING": "Dusunce", "BRAINLESS_FOLDER_DAILY": "Dusunce/Gunluk"}
            with mock.patch.dict(os.environ, env):
                for m in ("paths", "lint_wiki", "wiki_prune"):
                    sys.modules.pop(m, None)
                wp = importlib.import_module("wiki_prune")
                self.assertEqual(wp.INBOX, Path(vault) / "Gelen")
                self.assertEqual(wp.DECISIONS, Path(vault) / "Dusunce" / "Decisions")
                self.assertEqual(wp.CAPTURE_DIRS[1], Path(vault) / "Dusunce" / "Gunluk")
            for m in ("paths", "lint_wiki", "wiki_prune"):
                sys.modules.pop(m, None)


if __name__ == "__main__":
    unittest.main()
