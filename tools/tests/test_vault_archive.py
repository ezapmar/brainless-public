"""The backup, round-tripped: create, restore, verify, prune, on a throwaway vault.

A pass-through stand-in for `age` replaces the real encryption, so the test
proves the archive holds everything (private homes and git history included),
restores to the same files, and that only this tool's own archives are pruned.

Run: python3 -m unittest tools.tests.test_vault_archive -v
"""
import importlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

FAKE_AGE = """#!/bin/sh
# encrypt: age -r KEY  (stdin -> stdout); decrypt: age -d -i KEY FILE
if [ "$1" = "-d" ]; then cat "$4"; else cat; fi
"""


class Archive(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.vault, self.dest, bin_dir = base / "vault", base / "Drive" / "backups", base / "bin"
        for d in (self.vault, self.dest.parent, bin_dir):
            d.mkdir(parents=True)
        (bin_dir / "age").write_text(FAKE_AGE)
        (bin_dir / "age").chmod(stat.S_IRWXU)
        self.env = {k: os.environ.get(k) for k in ("BRAINLESS_VAULT", "BRAINLESS_BACKUP_DIR",
                                                   "BRAINLESS_BACKUP_RECIPIENT", "PATH")}
        os.environ.update({"BRAINLESS_VAULT": str(self.vault), "BRAINLESS_BACKUP_DIR": str(self.dest),
                           "BRAINLESS_BACKUP_RECIPIENT": "age1testrecipient",
                           "PATH": f"{bin_dir}:{os.environ['PATH']}"})
        self.addCleanup(self.restore_env)
        files = {"Personal/Alex - Health/lab.md": "private but backed up",
                 ".wiki/concepts/nakit.md": "---\nlang: tr\nsummary_en: x\n---\n# Nakit\n[[Nordhaven]]\n",
                 ".wiki/entities/Nordhaven.md": "---\nlang: tr\nsummary_en: x\n---\n# Nordhaven\n",
                 ".venv/lib/big.bin": "skip me", "logs/x.log": "skip me", "notes.md": "hello"}
        for rel, text in files.items():
            p = self.vault / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        (self.vault / "tools").symlink_to(ROOT / "tools")      # verify runs the real lint
        git = lambda *a: subprocess.run(["git", "-C", str(self.vault), *a], check=True, capture_output=True)
        git("init", "-q")
        git("add", "notes.md")
        git("-c", "user.email=x@x", "-c", "user.name=x", "commit", "-qm", "first")
        import vault_archive
        self.va = importlib.reload(vault_archive)

    def restore_env(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_round_trip_restores_everything_that_matters(self):
        path = self.va.create()
        self.assertTrue(path.name.startswith("brainless-") and path.name.endswith(".tar.gz.age"))
        self.assertFalse(list(self.dest.glob("*.part")))
        key = Path(self.tmp.name) / "test.key"
        key.write_text("AGE-SECRET-KEY-TEST")      # the fake age ignores it
        res = self.va.verify(key, path)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["missing"], 0)
        self.assertTrue(res["bundle_ok"])
        self.assertEqual((res["wiki_pages"], res["broken_links"]), (2, 0))
        s = self.va.status()
        self.assertEqual(s["created_days_ago"], 0)
        self.assertEqual(s["verified_days_ago"], 0)

    def test_private_homes_are_in_and_rebuildable_folders_are_out(self):
        listed = {str(p) for p in self.va.files(self.vault)}
        self.assertIn("Personal/Alex - Health/lab.md", listed)
        self.assertFalse(any(p.startswith((".venv", "logs", ".git/")) for p in listed))

    def test_not_due_means_nothing_is_made(self):
        self.va.save_state(created=datetime.now().isoformat(timespec="seconds"))
        self.assertIsNone(self.va.create(if_older_days=30))
        self.va.save_state(created=(datetime.now() - timedelta(days=31)).isoformat(timespec="seconds"))
        self.assertIsNotNone(self.va.create(if_older_days=30))

    def test_prune_keeps_six_and_touches_only_its_own_files(self):
        self.dest.mkdir(parents=True, exist_ok=True)
        for i in range(8):
            (self.dest / f"brainless-2026-0{i + 1}-01-0000.tar.gz.age").write_text("x")
        (self.dest / "someone-else.pdf").write_text("keep")
        gone = self.va.prune(self.dest)
        self.assertEqual([p.name[10:17] for p in gone], ["2026-01", "2026-02"])
        self.assertTrue((self.dest / "someone-else.pdf").exists())
        self.assertEqual(len(list(self.dest.glob("brainless-*"))), 6)

    def test_a_missing_key_file_refuses_before_touching_anything(self):
        with self.assertRaises(RuntimeError) as e:
            self.va.verify(Path(self.tmp.name) / "gone.key", Path("x"))
        self.assertIn("key file not found", str(e.exception))

    def test_missing_config_refuses_cleanly(self):
        os.environ["BRAINLESS_BACKUP_RECIPIENT"] = ""
        with self.assertRaises(RuntimeError):
            self.va.create()


if __name__ == "__main__":
    unittest.main()
