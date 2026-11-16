"""The nightly push guard in vault_backup.sh fails closed.

`git add -A` holds the whole vault, so the push must only reach a private
GitHub repo. A push URL the guard cannot parse used to skip the check and push
anyway; now it is refused. These tests run the real script against a throwaway
repo with a fake curl on PATH, so no network and no real remote is touched.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".agents" / "scripts" / "vault_backup.sh"


def slug_of(url):
    body = SCRIPT.read_text(encoding="utf-8")
    start = body.index("slug_of() {")
    end = body.index("\n}\n", start) + 3
    out = subprocess.run(["bash", "-c", body[start:end] + '\nslug_of "$1"', "_", url],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


class SlugParsing(unittest.TestCase):
    def test_standard_forms(self):
        for url in ("git@github.com:owner/vault.git", "https://github.com/owner/vault.git",
                    "https://github.com/owner/vault", "https://github.com/owner/vault/",
                    "ssh://git@github.com/owner/vault.git", "https://x-token@github.com/owner/vault.git"):
            self.assertEqual(slug_of(url), "owner/vault", url)

    def test_unparsable_forms_give_nothing(self):
        for url in ("github-alias:owner/vault.git", "git@example.org:owner/vault.git",
                    "https://bitbucket.org/owner/vault.git", "/tmp/vault.git", ""):
            self.assertEqual(slug_of(url), "", url)


class GuardFailsClosed(unittest.TestCase):
    def run_guard(self, push_url, http_code="404"):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            vault, bare, bin_dir = tmp / "vault", tmp / "remote.git", tmp / "bin"
            bin_dir.mkdir()
            subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
            subprocess.run(["git", "init", "-q", "-b", "master", str(vault)], check=True)
            for k, v in (("user.name", "t"), ("user.email", "t@example.invalid")):
                subprocess.run(["git", "-C", str(vault), "config", k, v], check=True)
            subprocess.run(["git", "-C", str(vault), "remote", "add", "origin", str(bare)], check=True)
            subprocess.run(["git", "-C", str(vault), "remote", "set-url", "--push", "origin", push_url], check=True)
            (vault / "note.md").write_text("x\n", encoding="utf-8")
            curl = bin_dir / "curl"
            curl.write_text(f"#!/bin/sh\nprintf '%s' '{http_code}'\n", encoding="utf-8")
            curl.chmod(0o755)
            env = dict(os.environ, BRAINLESS_VAULT=str(vault), PATH=f"{bin_dir}:{os.environ['PATH']}")
            out = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env, timeout=60)
            return out.stdout + out.stderr

    def test_unparsable_push_url_is_refused(self):
        self.assertIn("push refused", self.run_guard("github-alias:owner/vault.git"))

    def test_public_repo_is_refused(self):
        self.assertIn("public or unverifiable", self.run_guard("git@github.com:owner/vault.git", "200"))

    def test_public_engine_repo_is_refused_by_name(self):
        self.assertIn("public engine repo", self.run_guard("git@github.com:owner/brainless-public.git"))


if __name__ == "__main__":
    unittest.main()
