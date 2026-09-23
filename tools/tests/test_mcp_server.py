"""The read-only MCP server: protocol shape and, above all, what it refuses.

Run: python3 -m unittest tools.tests.test_mcp_server -v
"""
import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))


class Server(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old = os.environ.get("BRAINLESS_VAULT")
        os.environ["BRAINLESS_VAULT"] = self.tmp.name
        os.environ["BRAINLESS_SEARCH"] = "bm25"
        self.addCleanup(os.environ.pop, "BRAINLESS_SEARCH", None)
        self.addCleanup(lambda: os.environ.__setitem__("BRAINLESS_VAULT", self.old) if self.old
                        else os.environ.pop("BRAINLESS_VAULT", None))
        root = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        (root / ".gitignore").write_text(".wiki/**/*private-note*\n")
        for rel, body in ((".wiki/concepts/pricing.md", "payroll pricing tiers"),
                          (".wiki/summaries/private-note.md", "payroll pricing secret"),
                          (".wiki/summaries/work_finance_resources_pack.md", "payroll pricing numbers"),
                          ("Work/raw.md", "payroll pricing raw"),
                          (".wiki/INDEX.md", "- [[pricing]]\n- [[x_finance_resources_y]]")):
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"---\nsummary_en: {body}\n---\n{body}\n")
        import wiki_search
        import mcp_server
        importlib.reload(wiki_search)
        self.s = importlib.reload(mcp_server)

    def rpc(self, method, params=None, mid=1):
        return self.s.handle({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})

    def tool(self, name, **args):
        res = self.rpc("tools/call", {"name": name, "arguments": args})["result"]
        return res["isError"], res["content"][0]["text"]

    def test_initialize_echoes_a_known_protocol(self):
        r = self.rpc("initialize", {"protocolVersion": "2025-03-26"})["result"]
        self.assertEqual(r["protocolVersion"], "2025-03-26")
        self.assertIn("tools", r["capabilities"])

    def test_notification_gets_no_reply(self):
        self.assertIsNone(self.s.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_every_tool_is_read_only(self):
        tools = self.rpc("tools/list")["result"]["tools"]
        self.assertEqual({t["name"] for t in tools}, {"search", "read_page", "index"})
        self.assertTrue(all(t["annotations"]["readOnlyHint"] for t in tools))

    def test_search_leaves_out_ignored_and_denied_pages(self):
        err, text = self.tool("search", query="payroll pricing")
        paths = [r["path"] for r in json.loads(text)["results"]]
        self.assertEqual(paths, [".wiki/concepts/pricing.md"])

    def test_read_page_refuses_outside_wiki_ignored_and_denied(self):
        for p in ("Work/raw.md", "../etc/passwd", ".wiki/summaries/private-note.md",
                  ".wiki/summaries/work_finance_resources_pack.md"):
            err, _ = self.tool("read_page", path=p)
            self.assertTrue(err, p)
        err, text = self.tool("read_page", path=".wiki/concepts/pricing.md")
        self.assertFalse(err)
        self.assertIn("payroll pricing tiers", text)

    def test_index_drops_lines_naming_denied_pages(self):
        err, text = self.tool("index")
        self.assertIn("[[pricing]]", text)
        self.assertNotIn("finance_resources", text)

    def test_unknown_method_is_an_error(self):
        self.assertEqual(self.rpc("resources/write")["error"]["code"], -32601)


if __name__ == "__main__":
    unittest.main()
