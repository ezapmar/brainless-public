"""Unit tests for tools/vault_graph.py and its health_check integration.

Run: PYTHONPATH=tools python3 -m unittest tools.tests.test_vault_graph -v
"""
import importlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import vault_graph as vg


class VaultGraphTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, rel: str, text: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def test_single_connected_component_and_raw_inbound(self):
        self.write("Work/Proj/notes.md", "# Proj\n\n- [[Work/Proj/doc|doc]]\n- [[Work/Proj/doc_raw|raw]]\n")
        self.write("Work/Proj/doc.md", "# Doc\n\nLinked from hub.\n")
        self.write("Work/Proj/doc_raw.md", "Raw converted output with zero outbound links.\n")
        res = vg.compute(self.root, check_gitignore=False)
        self.assertEqual(res["nodes"], 3)
        self.assertEqual(res["orphan_count"], 0)
        self.assertEqual(res["components"], 1)
        self.assertEqual(res["main_share"], 1.0)
        self.assertEqual(vg.worst_status(res), "OK")

    def test_detects_orphans_and_disconnected_islands(self):
        self.write("Work/A/hub.md", "[[Work/A/a1]]\n")
        self.write("Work/A/a1.md", "leaf\n")
        self.write("Work/B/b1.md", "[[Work/B/b2]]\n")
        self.write("Work/B/b2.md", "pair\n")
        self.write("Work/C/alone.md", "no links anywhere\n")
        res = vg.compute(self.root, check_gitignore=False)
        self.assertEqual(res["orphans"], ["Work/C/alone.md"])
        self.assertEqual(res["components"], 3)
        self.assertEqual(vg.worst_status(res), "RED")

    def test_resolves_url_encoded_markdown_links_with_special_chars(self):
        self.write(
            "Library/Books/Index.md",
            "[Outliers](Thinking/Outliers%20%5BGladwell%5D.md)\n",
        )
        self.write("Library/Books/Thinking/Outliers [Gladwell].md", "book text\n")
        res = vg.compute(self.root, check_gitignore=False)
        self.assertEqual(res["orphan_count"], 0)
        self.assertEqual(res["components"], 1)

    def test_work_personal_cross_domain_allowlist_from_profile_and_unexpected(self):
        self.write(
            "_Agent-Context/PROFILE.md",
            "---\nowner_name: Test\ncross_domain_allow: Personal/Relocation/ -> Work/Expansion/\n---\n",
        )
        # Allowlisted bridge from PROFILE.md: Relocation <-> Expansion
        self.write("Personal/Relocation/notes.md", "[[Work/Expansion/notes]]\n")
        self.write("Work/Expansion/notes.md", "Expansion hub\n")
        # Default allowlisted bridge: Personal/Career.md -> any Work note
        self.write("Personal/Career.md", "[[Work/Expansion/notes]]\n")
        # Unexpected bridge: arbitrary Personal note <-> arbitrary Work note
        self.write("Personal/Random/diary.md", "[[Work/ProjectX/notes]]\n")
        self.write("Work/ProjectX/notes.md", "ProjectX\n")
        res = vg.compute(self.root, check_gitignore=False)
        self.assertEqual(res["cross_allowed_count"], 2)
        self.assertEqual(res["cross_unexpected_count"], 1)
        self.assertEqual(
            res["cross_unexpected"],
            [("Personal/Random/diary.md", "Work/ProjectX/notes.md")],
        )

    def test_gitignore_leak_detected_and_local_hub_allowed(self):
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        self.write(".gitignore", "Personal/Secret/\n")
        self.write("Personal/Public/hub.md", "[[Personal/Secret/private-doc]]\n")
        self.write("Personal/Secret/README.md", "[[Personal/Secret/private-doc]]\n[[Personal/Public/hub]]\n")
        self.write("Personal/Secret/private-doc.md", "secret\n")
        res = vg.compute(self.root, check_gitignore=True)
        self.assertEqual(res["gitignore_leak_count"], 1)
        self.assertEqual(
            res["gitignore_leaks"],
            [("Personal/Public/hub.md", "Personal/Secret/private-doc.md")],
        )
        self.assertEqual(vg.worst_status(res), "RED")

        # Removing the leak from tracked hub leaves local-only hub linking upward: 0 leaks
        self.write("Personal/Public/hub.md", "# Public Hub\n")
        res_clean = vg.compute(self.root, check_gitignore=True)
        self.assertEqual(res_clean["gitignore_leak_count"], 0)
        self.assertEqual(res_clean["orphan_count"], 0)
        self.assertEqual(vg.worst_status(res_clean), "OK")

    def test_health_check_vault_graph_findings_thresholds(self):
        import health_check as hc
        importlib.reload(hc)
        ok_data = {
            "nodes": 100, "edges": 200, "avg_degree": 4.0,
            "orphans": 0, "components": 1, "main_share": 1.0,
            "cross_unexpected": 0, "gitignore_leaks": 0,
        }
        self.assertEqual(hc.vault_graph_findings(ok_data, 60)[0], "OK")
        self.assertEqual(hc.vault_graph_findings({**ok_data, "orphans": 2}, 60)[0], "WARN")
        self.assertEqual(hc.vault_graph_findings({**ok_data, "gitignore_leaks": 1}, 60)[0], "RED")
        self.assertEqual(hc.vault_graph_findings(ok_data, hc.RED_FLAG_SECONDS + 10)[0], "RED")

    def test_respects_obsidian_graph_json_exclusions(self):
        self.write(".obsidian/graph.json", json.dumps({"search": "-path:raw/ -path:docs/"}))
        self.write("raw/unlinked.md", "immutable raw\n")
        self.write("docs/unlinked.md", "doc\n")
        self.write("Work/A/a.md", "[[Work/A/b]]\n")
        self.write("Work/A/b.md", "b\n")
        res = vg.compute(self.root, check_gitignore=False)
        self.assertEqual(res["nodes"], 2)
        self.assertEqual(res["orphan_count"], 0)


if __name__ == "__main__":
    unittest.main()
