"""`brainless init` and `brainless doctor`, without a model, a scheduler or a network.

The installer is the first thing a stranger meets, so the tests follow what a
stranger would do: take every default, or answer the questions one by one. The
promises checked: the folders exist in the chosen language, the settings land
in brainless.toml and read back, a key goes to the secret store and never into
the file, the notes stay out of git, an outside folder is linked and not
copied, and a second run keeps what the first one wrote.
"""
from pathlib import Path
import contextlib
import io
import os
import subprocess
import tempfile
import tomllib
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
import config
import doctor
import init_wizard as wiz
import profiles


class WizardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.vault = base / "vault"
        self.vault.mkdir()
        subprocess.run(["git", "init", "-q", str(self.vault)], check=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("BRAINLESS_", "ANTHROPIC_"))}
        env.update(BRAINLESS_VAULT=str(self.vault), BRAINLESS_SECRETS_BACKEND="file",
                   XDG_CONFIG_HOME=str(base / "cfg"))
        for patcher in (mock.patch.dict(os.environ, env, clear=True),
                        mock.patch.object(wiz, "probe", return_value=True),
                        contextlib.redirect_stdout(io.StringIO())):
            patcher.__enter__()
            self.addCleanup(patcher.__exit__, None, None, None)

    def run_wizard(self, argv, answers=None):
        args = wiz.parse(argv + ["--no-schedule", "--no-first-run", "--no-search"])
        return wiz.run(args, wiz.Prompter(args.yes, answers))

    def toml(self):
        with open(self.vault / "brainless.toml", "rb") as fh:
            return tomllib.load(fh)


class TestUnattended(WizardTest):
    def test_defaults_in_turkish(self):
        self.assertEqual(self.run_wizard(["--yes", "--name", "Ada", "--lang", "tr",
                                          "--provider", "ollama", "--model", "m1"]), 0)
        data = self.toml()
        self.assertEqual(data["profile"], "lite")
        self.assertEqual(data["owner"], {"name": "Ada", "lang": "tr"})
        self.assertEqual(data["folders"]["daily"], "Notlar")
        self.assertEqual(data["llm"], {"provider": "ollama", "model": "m1"})
        self.assertFalse(data["schedule"]["enabled"])
        for rel in ("Gelen", "Notlar", "Kütüphane", "Düşünce/Kararlar"):
            self.assertTrue((self.vault / rel).is_dir(), rel)
        env = config.to_env(data)
        self.assertEqual(env["BRAINLESS_FOLDER_INBOX"], "Gelen")

    def test_notes_stay_out_of_git(self):
        self.run_wizard(["--yes", "--lang", "tr", "--provider", "ollama", "--model", "m"])
        (self.vault / "Notlar" / "private.md").write_text("x")
        out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=self.vault,
                             capture_output=True, text=True).stdout
        self.assertNotIn("Notlar", out)
        self.run_wizard(["--yes", "--lang", "tr", "--provider", "ollama", "--model", "m", "--force"])
        exclude = (self.vault / ".git" / "info" / "exclude").read_text()
        self.assertEqual(exclude.count("/Notlar/"), 1)

    def test_existing_config_is_kept(self):
        self.run_wizard(["--yes", "--name", "First", "--provider", "ollama", "--model", "m"])
        self.run_wizard(["--yes", "--name", "Second", "--provider", "ollama", "--model", "m"])
        self.assertEqual(self.toml()["owner"]["name"], "First")

    def test_key_from_env_goes_to_the_store_not_the_file(self):
        os.environ["MY_KEY"] = "sk-ant-secret"
        self.run_wizard(["--yes", "--provider", "anthropic", "--api-key-env", "MY_KEY"])
        self.assertEqual(config.get_secret("anthropic_api_key"), "sk-ant-secret")
        text = (self.vault / "brainless.toml").read_text()
        self.assertNotIn("sk-ant-secret", text)
        self.assertEqual(self.toml()["llm"]["model"], "claude-opus-5")

    def test_unknown_provider_stops(self):
        with self.assertRaises(SystemExit):
            self.run_wizard(["--yes", "--provider", "gpt-9000"])
        self.assertFalse((self.vault / "brainless.toml").exists())


class TestInteractive(WizardTest):
    def test_questions_answered_one_by_one(self):
        with mock.patch("llm.list_models", return_value=None):
            code = self.run_wizard([], answers=[
                "Ada",          # name
                "en",           # language
                "n",            # rename folders?
                "n",            # another notes folder?
                "2",            # a cloud API with a key
                "3",            # Grok
                "xai-typed",    # the key (hidden)
                "grok-test",    # model
            ])
        self.assertEqual(code, 0)
        self.assertEqual(self.toml()["llm"], {"provider": "grok", "model": "grok-test"})
        self.assertEqual(config.get_secret("xai_api_key"), "xai-typed")
        self.assertEqual(self.toml()["folders"]["daily"], "Notes")

    def test_rename_thinking_moves_its_children(self):
        self.run_wizard(["--provider", "ollama", "--model", "m"], answers=[
            "Ada", "en", "y", "", "", "", "Mind", "n"])
        folders = self.toml()["folders"]
        self.assertEqual(folders["thinking"], "Mind")
        self.assertEqual(folders["decisions"], "Mind/Decisions")
        self.assertTrue((self.vault / "Mind" / "Beliefs").is_dir())


class TestLinkExternal(WizardTest):
    def test_link_is_read_only_and_a_source(self):
        notes = Path(self.tmp.name) / "old-vault"
        notes.mkdir()
        (notes / "a.md").write_text("# a")
        self.run_wizard(["--yes", "--provider", "ollama", "--model", "m", "--extra", str(notes)])
        link = self.vault / "Library" / "_linked" / "old-vault"
        self.assertTrue(link.is_symlink())
        self.assertEqual(self.toml()["folders"]["sources"], ["Library/_linked/old-vault"])
        self.assertEqual(sorted(p.name for p in notes.iterdir()), ["a.md"])

    def test_bad_paths_skipped(self):
        self.assertIsNone(wiz.link_external(str(self.vault), "Library", "/no/such/folder"))
        (self.vault / "inside").mkdir()
        self.assertIsNone(wiz.link_external(str(self.vault), "Library", str(self.vault / "inside")))


class TestDoctor(WizardTest):
    def levels(self):
        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=1)), \
             mock.patch("llm.list_models", return_value=["m"]):
            return {msg: level for level, msg in doctor.checks()}

    def test_fresh_vault_fails_until_init(self):
        self.assertIn("FAIL", self.levels().values())

    def test_after_init_only_warnings(self):
        self.run_wizard(["--yes", "--provider", "ollama", "--model", "m"])
        config.apply(force=True)
        levels = self.levels()
        self.assertNotIn("FAIL", levels.values(), levels)

    def test_missing_key_is_a_failure(self):
        self.run_wizard(["--yes", "--provider", "openai", "--model", "gpt-x"])
        config.apply(force=True)
        with mock.patch("llm._KEYS", {}):
            fails = [m for m, lvl in self.levels().items() if lvl == "FAIL"]
        self.assertTrue(any("openai key" in m for m in fails), fails)



class TestProfiles(WizardTest):
    def test_full_profile_hides_nothing(self):
        self.assertTrue(profiles.allowed("closeout"))

    def test_lite_hides_full_only_until_enabled(self):
        self.run_wizard(["--yes", "--provider", "ollama", "--model", "m"])
        config.apply(force=True)
        self.assertTrue(profiles.allowed("compile"))
        self.assertFalse(profiles.allowed("media"))
        with mock.patch("sys.stderr"):
            self.assertEqual(profiles.main(["check", "media"]), 3)
        self.assertEqual(profiles.main(["enable", "media"]), 0)
        self.assertEqual(self.toml()["commands"]["enable"], ["media"])
        os.environ["BRAINLESS_COMMANDS"] = "media"
        self.assertTrue(profiles.allowed("media"))
        profiles.main(["enable", "graph"])
        self.assertEqual(self.toml()["commands"]["enable"], ["graph", "media"])
        os.environ["BRAINLESS_COMMANDS"] = "graph,media"
        profiles.main(["disable", "media"])
        self.assertEqual(self.toml()["commands"]["enable"], ["graph"])
        self.assertEqual(self.toml()["llm"]["provider"], "ollama")   # the rest of the file intact

if __name__ == "__main__":
    unittest.main()
