"""The cross-platform dispatcher (tools/cli.py), the portable lock, and the
Windows scheduler definition. Portable on purpose: CI runs it on Windows too."""
import os
import subprocess
import sys
import tempfile
import unittest
import xml.dom.minidom
from unittest import mock

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.dirname(TOOLS)

import cli
import vault_lock
import schedule


class DispatchTable(unittest.TestCase):
    def test_every_command_points_at_a_tool(self):
        for cmd, (script, _) in cli.TOOLS.items():
            self.assertTrue(os.path.isfile(os.path.join(TOOLS, script)), f"{cmd} -> {script}")

    def test_help_lists_every_command(self):
        for cmd in [*cli.TOOLS, "dialectic", "concepts", "health", "update", "vault", "version"]:
            self.assertIn(f"\n  {cmd}", cli.HELP, cmd)

    def test_gated_commands_are_known(self):
        self.assertLessEqual(cli.GATED, set(cli.TOOLS) | {"health"})

    def test_dialectic_thesis_becomes_topic_and_local(self):
        argv = cli.dialectic_argv(["AI replaces HR", "--parallel"])
        self.assertEqual(argv[2:], ["--local", "--topic", "AI replaces HR", "--parallel"])

    def test_dialectic_buzz_drops_local(self):
        argv = cli.dialectic_argv(["--run", "noon", "--buzz"])
        self.assertEqual(argv[2:], ["--run", "noon"])

    def test_concepts_subcommands(self):
        self.assertEqual(cli.concepts_argv([])[2:], ["--list"])
        self.assertEqual(cli.concepts_argv(["review"])[2:], ["--review"])
        self.assertEqual(cli.concepts_argv(["decide", "x", "yes"])[2:], ["--decide", "x", "yes"])


class VaultResolution(unittest.TestCase):
    def test_env_wins(self):
        with mock.patch.dict(os.environ, {"BRAINLESS_VAULT": "/somewhere"}):
            self.assertEqual(cli.resolve_vault(), "/somewhere")

    def test_pointer_file_with_bom(self):
        """Windows PowerShell 5.1 writes a BOM with -Encoding utf8; it must not end up in the path."""
        with tempfile.TemporaryDirectory() as home:
            conf = os.path.join(home, ".config", "brainless")
            os.makedirs(conf)
            with open(os.path.join(conf, "vault"), "w", encoding="utf-8-sig") as fh:
                fh.write("/vault/here\n")
            env = {k: v for k, v in os.environ.items() if k != "BRAINLESS_VAULT"}
            env.update(HOME=home, USERPROFILE=home)
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(cli.resolve_vault(), "/vault/here")

    def test_default_is_home_brainless(self):
        with tempfile.TemporaryDirectory() as home:
            env = {k: v for k, v in os.environ.items() if k != "BRAINLESS_VAULT"}
            env.update(HOME=home, USERPROFILE=home)
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(cli.resolve_vault(), os.path.join(home, "brainless"))


class EndToEnd(unittest.TestCase):
    def run_cli(self, *args):
        env = dict(os.environ, BRAINLESS_VAULT=ENGINE)
        return subprocess.run([sys.executable, os.path.join(TOOLS, "cli.py"), *args],
                              capture_output=True, text=True, env=env, timeout=60)

    def test_version_and_vault(self):
        self.assertEqual(self.run_cli("version").stdout.strip(),
                         open(os.path.join(ENGINE, "VERSION"), encoding="utf-8").read().strip())
        self.assertEqual(os.path.realpath(self.run_cli("vault").stdout.strip()), os.path.realpath(ENGINE))

    def test_unknown_command_exits_2(self):
        r = self.run_cli("no-such-command")
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown command", r.stderr)

    def test_missing_vault_exits_1(self):
        env = dict(os.environ, BRAINLESS_VAULT=os.path.join(tempfile.gettempdir(), "no-vault-here"))
        r = subprocess.run([sys.executable, os.path.join(TOOLS, "cli.py"), "search", "x"],
                           capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 1)
        self.assertIn("no vault", r.stderr)


class Shims(unittest.TestCase):
    def test_cmd_shim_has_crlf_and_calls_cli(self):
        raw = open(os.path.join(ENGINE, "bin", "brainless.cmd"), "rb").read()
        self.assertIn(b"\r\n", raw)
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
        self.assertIn(b"tools\\cli.py", raw)

    def test_posix_shim_hands_over_to_cli(self):
        text = open(os.path.join(ENGINE, "bin", "brainless"), encoding="utf-8").read()
        self.assertIn('exec "$PY" "$VAULT/tools/cli.py" "$@"', text)


class Lock(unittest.TestCase):
    def test_exclusive_lock_takes_and_releases(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.lock")
            for _ in range(2):          # the second round proves the first released it
                with open(path, "a") as fh:
                    vault_lock.lock_exclusive(fh)


class WindowsTask(unittest.TestCase):
    def test_task_xml_parses_and_carries_the_settings(self):
        with mock.patch.dict(os.environ, {"USERNAME": "ada", "USERDOMAIN": "PC"}):
            body = schedule.task_xml()
        doc = xml.dom.minidom.parseString(body.replace('encoding="UTF-16"', 'encoding="UTF-8"').encode())
        text = doc.toxml()
        for want in ("PT15M", "<StartWhenAvailable>true", "<DisallowStartIfOnBatteries>false",
                     "<LogonTrigger>", "PC\\ada", "jobqueue.py", "-X utf8"):
            self.assertIn(want, text)

    def test_task_xml_without_logon_trigger(self):
        self.assertNotIn("LogonTrigger", schedule.task_xml(logon=False))

    def test_paths_with_spaces_are_quoted(self):
        with mock.patch.object(schedule, "ENGINE", os.path.join("C:", "Users", "Ada Lovelace", "brainless")):
            self.assertIn('"', schedule.task_xml().split("<Arguments>")[1].split("</Arguments>")[0])



class CronFallback(unittest.TestCase):
    def test_no_systemctl_means_cron(self):
        with mock.patch.object(schedule.sys, "platform", "linux"), \
                mock.patch.object(schedule.shutil, "which", return_value=None):
            self.assertEqual(schedule.platform(), "cron")

    def test_cron_line_is_marked_and_quoted(self):
        with mock.patch.object(schedule, "ENGINE", "/home/a b/it's"):
            line = schedule.cron_line()
        self.assertTrue(line.startswith("*/15 * * * * cd "))
        self.assertTrue(line.endswith(schedule.CRON_MARK))
        self.assertIn("'/home/a b/it'\\''s'", line)

    def test_install_replaces_its_own_line_only(self):
        seen = {}
        with mock.patch.object(schedule, "platform", return_value="cron"), \
                mock.patch.object(schedule.shutil, "which", return_value="/usr/bin/crontab"), \
                mock.patch.object(schedule, "_crontab", return_value="0 1 * * * other\nold " + schedule.CRON_MARK + "\n"), \
                mock.patch.object(schedule, "_set_crontab", side_effect=lambda lines: seen.setdefault("l", lines) and
                                  mock.Mock(returncode=0)), \
                mock.patch.dict(os.environ, {"BRAINLESS_PROFILE": "lite"}), \
                mock.patch("builtins.print"):
            self.assertEqual(schedule.install(), 0)
        self.assertEqual(seen["l"][0], "0 1 * * * other")
        self.assertEqual(len(seen["l"]), 2)
        self.assertTrue(seen["l"][1].endswith(schedule.CRON_MARK))

    def test_loaded_survives_a_missing_binary(self):
        with mock.patch.object(schedule, "platform", return_value="linux"), \
                mock.patch.object(schedule, "_run", side_effect=FileNotFoundError):
            self.assertFalse(schedule.loaded())


if __name__ == "__main__":
    unittest.main()
