"""Chat export import on synthetic exports of both shapes.

No real history is needed or touched: the tests build a Claude export and a
ChatGPT export by hand and check what lands, what is skipped, and that a
second run changes nothing.

Run: python3 -m unittest tools.tests.test_chat_import -v
"""
import contextlib
import importlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

LONG = " ".join(["pricing"] * 200)
CLAUDE = [{
    "uuid": "c-1", "name": "Pricing tiers for SMB", "created_at": "2025-03-01T10:00:00Z",
    "chat_messages": [
        {"sender": "human", "text": f"How should I price? {LONG}"},
        {"sender": "assistant", "content": [{"type": "text", "text": "Start from value. token ghp_" + "a" * 36}]},
    ]}, {
    "uuid": "c-2", "name": "quick", "created_at": "2025-03-02T10:00:00Z",
    "chat_messages": [{"sender": "human", "text": "hi"}]}, {
    "uuid": "c-3", "name": "Health checkup results", "created_at": "2025-03-03T10:00:00Z",
    "chat_messages": [{"sender": "human", "text": LONG}]},
]
CHATGPT = [{
    "conversation_id": "g-1", "title": "Hiring plan", "create_time": 1735732800,
    "current_node": "n3",
    "mapping": {
        "root": {"id": "root", "message": None, "parent": None},
        "n1": {"id": "n1", "parent": "root", "message": {"author": {"role": "user"},
               "content": {"content_type": "text", "parts": [f"Plan hiring {LONG}"]}}},
        "n2x": {"id": "n2x", "parent": "n1", "message": {"author": {"role": "assistant"},
                "content": {"content_type": "text", "parts": ["REGENERATED AWAY"]}}},
        "n2": {"id": "n2", "parent": "n1", "message": {"author": {"role": "assistant"},
               "content": {"content_type": "text", "parts": ["Kept answer"]}}},
        "n3": {"id": "n3", "parent": "n2", "message": {"author": {"role": "user"},
               "content": {"content_type": "text", "parts": ["Thanks " + chr(0x2014) + " done"]}}},
    }}]


class ChatImport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old = os.environ.get("BRAINLESS_VAULT")
        os.environ["BRAINLESS_VAULT"] = self.tmp.name
        self.addCleanup(lambda: os.environ.__setitem__("BRAINLESS_VAULT", self.old) if self.old
                        else os.environ.pop("BRAINLESS_VAULT", None))
        import chat_import
        self.ci = importlib.reload(chat_import)
        self.root = Path(self.tmp.name)
        self.claude = self.root / "claude.json"
        self.claude.write_text(json.dumps(CLAUDE))
        self.gpt = self.root / "gpt.json"
        self.gpt.write_text(json.dumps(CHATGPT))

    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.ci.main(list(args))
        return out.getvalue()

    def test_both_shapes_parse_and_chatgpt_follows_the_kept_branch(self):
        convs = self.ci.parse(CLAUDE) + self.ci.parse(CHATGPT)
        self.assertEqual([c["source"] for c in convs], ["claude"] * 3 + ["chatgpt"])
        gpt = convs[-1]
        self.assertEqual(gpt["date"], "2025-01-01")
        self.assertEqual([r for r, _ in gpt["turns"]], ["user", "assistant", "user"])
        self.assertNotIn("REGENERATED", json.dumps(gpt["turns"]))

    def test_triage_writes_nothing(self):
        out = self.run_cli("triage", str(self.claude))
        self.assertIn("short", out)
        self.assertIn("personal", out)
        self.assertFalse((self.root / "raw").exists())

    def test_import_skips_short_and_personal_redacts_secrets_and_is_idempotent(self):
        out = self.run_cli("import", str(self.claude), str(self.gpt))
        self.assertIn("imported 2", out)
        files = sorted((self.root / "raw/chats").rglob("*.md"))
        self.assertEqual([f.parent.name for f in files], ["chatgpt", "claude"])
        text = (self.root / "raw/chats/claude/2025-03-01-pricing-tiers-for-smb.md").read_text()
        self.assertIn("conversation_id: c-1", text)
        self.assertIn("[redacted github token]", text)
        self.assertNotIn("ghp_", text)
        self.assertNotIn(chr(0x2014), files[0].read_text())
        self.assertIn("imported 0", self.run_cli("import", str(self.claude), str(self.gpt)))

    def test_promote_moves_only_imported_chats(self):
        self.run_cli("import", str(self.claude))
        src = self.root / "raw/chats/claude/2025-03-01-pricing-tiers-for-smb.md"
        with contextlib.redirect_stderr(io.StringIO()):
            self.run_cli("promote", str(src), str(self.claude))
        self.assertTrue((self.root / "Library/Chats" / src.name).exists())
        self.assertFalse(src.exists())
        self.assertTrue(self.claude.exists())


if __name__ == "__main__":
    unittest.main()
