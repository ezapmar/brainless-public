"""Regression tests for the 2026-09-28 public audit fixes."""
import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))

import llm  # noqa: E402
import mac_notify  # noqa: E402

PAYLOAD = 'x" & (do shell script "touch /tmp/pwned") & ".pdf'


class MacNotifyTest(unittest.TestCase):
    def test_text_is_an_argument_not_script(self):
        cmd = mac_notify.command(PAYLOAD, 'ti"tle')
        scripts = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-e"]
        self.assertFalse(any("do shell script" in s for s in scripts))
        self.assertEqual(cmd[-2:], [PAYLOAD, 'ti"tle'])


class ClaudeToolFenceTest(unittest.TestCase):
    def _cmd(self, **kw):
        seen = {}

        def fake_run(cmd, **_):
            seen["cmd"] = cmd
            return mock.Mock(returncode=0, stdout="ok", stderr="")
        with mock.patch.object(llm, "resolve_claude", return_value="/bin/claude"), \
                mock.patch.object(llm.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(llm, "_record"):
            llm._run_claude_cli("p", 10, **kw)
        return seen["cmd"]

    def _tools(self, cmd):
        return cmd[cmd.index("--tools") + 1]

    def test_plain_call_has_no_tools_and_no_mcp(self):
        cmd = self._cmd()
        self.assertEqual(self._tools(cmd), "")
        self.assertIn("--strict-mcp-config", cmd)

    def test_web_call_cannot_read_files(self):
        tools = self._tools(self._cmd(web=True)).split(",")
        self.assertEqual(sorted(tools), ["WebFetch", "WebSearch"])

    def test_photo_call_reads_but_has_no_web(self):
        self.assertEqual(self._tools(self._cmd(allowed_tools=["Read"])), "Read")


class SandboxSettingsTest(unittest.TestCase):
    def _load(self, rel):
        with open(os.path.join(ROOT, rel)) as fh:
            return json.load(fh)["permissions"]

    def test_no_owner_home_and_read_scoped(self):
        for rel in (".agents/buzz/personas/settings.json", ".agents/buzz/agents/settings.json"):
            with open(os.path.join(ROOT, rel)) as fh:
                self.assertNotIn("/home/", fh.read(), rel)
            perms = self._load(rel)
            self.assertNotIn("Read", perms["allow"], rel)
            self.assertIn("Read(//__VAULT__/**)", perms["allow"], rel)
            self.assertIn("Read(~/.*/**)", perms["deny"], rel)
            self.assertNotIn("WebFetch", perms["allow"], rel)


if __name__ == "__main__":
    unittest.main()
