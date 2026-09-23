"""The Claude Code hooks, tested on the calls they must stop and the ones they must not.

A guard that blocks too much gets switched off, and one that blocks too little
is decoration, so every check has a case on each side. Dash characters are
built with chr() so this file itself stays clean.

Run: python3 -m unittest tools.tests.test_claude_guard -v
"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "hooks" / "claude_guard.py"
EM, EN = chr(0x2014), chr(0x2013)

spec = importlib.util.spec_from_file_location("claude_guard", SCRIPT)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def run(sub, payload):
    """Run the hook the way Claude Code does and return (decision, reason, raw stdout)."""
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    out = subprocess.run([sys.executable, str(SCRIPT), sub], input=raw, capture_output=True,
                         text=True, env={**os.environ, "BRAINLESS_VAULT": str(ROOT)})
    assert out.returncode == 0, out.stderr
    if not out.stdout.strip():
        return None, "", ""
    data = json.loads(out.stdout)
    spec = data.get("hookSpecificOutput", {})
    return spec.get("permissionDecision") or data.get("decision"), \
        spec.get("permissionDecisionReason") or data.get("reason", ""), out.stdout


def write(path, content):
    return run("pre", {"tool_name": "Write", "tool_input": {"file_path": str(path), "content": content}})


def edit(path, old, new):
    return run("pre", {"tool_name": "Edit",
                       "tool_input": {"file_path": str(path), "old_string": old, "new_string": new}})


def bash(cmd):
    return run("pre", {"tool_name": "Bash", "tool_input": {"command": cmd}})


WIKI = ROOT / ".wiki" / "digests" / "queries" / "guard-test.md"


class Dashes(unittest.TestCase):
    def test_a_new_dash_is_denied_in_every_write_tool(self):
        self.assertEqual(write(WIKI, f"a {EM} b")[0], "deny")
        self.assertEqual(edit(WIKI, "a", f"a {EN} b")[0], "deny")
        self.assertEqual(run("pre", {"tool_name": "MultiEdit", "tool_input": {
            "file_path": str(WIKI), "edits": [{"old_string": "x", "new_string": f"y{EM}"}]}})[0], "deny")
        self.assertEqual(run("pre", {"tool_name": "NotebookEdit", "tool_input": {
            "notebook_path": str(ROOT / "n.ipynb"), "new_source": EM}})[0], "deny")

    def test_a_dash_in_a_file_name_is_denied(self):
        self.assertEqual(write(ROOT / f".wiki/a {EM} b.md", "x")[0], "deny")

    def test_editing_near_a_legacy_dash_is_allowed(self):
        self.assertIsNone(edit(WIKI, f"old {EM} line", f"old {EM} line, fixed")[0])

    def test_bash_heredocs_and_commit_messages_are_covered(self):
        self.assertEqual(bash(f"git commit -m 'fix {EM} again'")[0], "deny")
        self.assertIsNone(bash("grep -P '\\x{2014}' file.md")[0])


class HumanAreas(unittest.TestCase):
    def test_human_areas_ask_and_the_wiki_and_drafts_do_not(self):
        self.assertEqual(write(ROOT / "Work/x.md", "hi")[0], "ask")
        self.assertEqual(edit(ROOT / "Personal/Content/c.md", "a", "b")[0], "ask")
        self.assertIsNone(write(ROOT / "Writings/Drafts/pitch.md", "hi")[0])
        self.assertIsNone(write(WIKI, "hi")[0])
        self.assertIsNone(write("/private/tmp/scratch.md", "hi")[0])


class Secrets(unittest.TestCase):
    def test_real_secret_shapes_are_denied(self):
        token = "ghp_" + "a" * 36
        iban = "TR" + "3" * 24
        self.assertEqual(write(WIKI, f"token {token}")[0], "deny")
        self.assertEqual(bash(f"echo {iban} >> notes.md")[0], "deny")

    def test_noisy_patterns_do_not_block(self):
        self.assertIsNone(write(WIKI, "phone 0532" + "1234567, event " + "a" * 64)[0])


class Briefings(unittest.TestCase):
    def test_a_briefing_outside_its_home_is_denied(self):
        self.assertEqual(write(ROOT / "daily-briefing-2026-09-22.md", "x")[0], "deny")
        self.assertIsNone(write(ROOT / "Daily Briefings/daily-briefing-2026-09-22.md", "x")[0])
        self.assertIsNone(write(ROOT / "tools/buzz_briefing_sync.sh", "x")[0])


class Deletions(unittest.TestCase):
    def test_deleting_outside_temp_asks_and_temp_cleanup_passes(self):
        for cmd in ("rm notes.md", "ls && rm -rf Work/old", "git rm x.md", "git reset --hard HEAD",
                    "git push --force origin master", "find . -name '*.tmp' -delete",
                    "git checkout .", "git -C /x clean -fd"):
            self.assertEqual(bash(cmd)[0], "ask", cmd)
        for cmd in ("rm -rf /private/tmp/claude-501/x", "rm -f /tmp/a /tmp/b", "git status",
                    "git push origin master", "git checkout -b feature", "echo rm is a word"):
            self.assertIsNone(bash(cmd)[0], cmd)


class WikiFrontmatter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / ".wiki")
        self.addCleanup(self.tmp.cleanup)

    def post(self, name, text):
        p = Path(self.tmp.name) / name
        p.write_text(text)
        return run("post", {"tool_name": "Write", "tool_input": {"file_path": str(p)}})

    def test_missing_keys_block_with_feedback(self):
        decision, reason, _ = self.post("page.md", "---\nlang: tr\n---\n# x\n")
        self.assertEqual(decision, "block")
        self.assertIn("summary_en", reason)

    def test_a_complete_page_and_underscore_files_pass(self):
        self.assertIsNone(self.post("ok.md", "---\nlang: tr\nsummary_en: x\n---\n# x\n")[0])
        self.assertIsNone(self.post("_lint-report.md", "# report\n")[0])

    def test_files_outside_the_wiki_are_ignored(self):
        self.assertIsNone(run("post", {"tool_name": "Write",
                                       "tool_input": {"file_path": str(ROOT / "README.md")}})[0])


class FailOpen(unittest.TestCase):
    def test_garbage_input_and_unknown_tools_never_block(self):
        self.assertEqual(run("pre", "not json")[0], None)
        self.assertEqual(run("pre", {"tool_name": "Read", "tool_input": {"file_path": "x"}})[0], None)
        out = subprocess.run([sys.executable, str(SCRIPT), "bogus"], input="{}", capture_output=True, text=True)
        self.assertEqual(out.returncode, 0)


class SessionContext(unittest.TestCase):
    def test_both_context_files_are_injected(self):
        _, _, raw = run("session", {})
        ctx = json.loads(raw)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("_Agent-Context/CONTEXT.md", ctx)
        self.assertIn("_Agent-Context/PROJECTS-ACTIVE.md", ctx)


if __name__ == "__main__":
    unittest.main()
