"""Tests for dead-end detection in tools/lint_wiki.py."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import lint_wiki


class DeadendPagesTest(unittest.TestCase):
    def test_deadends_have_inbound_and_zero_outbound_and_do_not_overlap_orphans(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wiki = root / ".wiki"
            (wiki / "concepts").mkdir(parents=True)
            (wiki / "_commands").mkdir(parents=True)

            hub = wiki / "concepts" / "hub.md"
            dead = wiki / "concepts" / "dead.md"
            orphan = wiki / "concepts" / "orphan.md"
            index = wiki / "INDEX.md"
            cmd = wiki / "_commands" / "example.md"

            hub.write_text("# Hub\nSee [[dead]].\n", encoding="utf-8")
            dead.write_text("# Dead\nNo outgoing links here.\n", encoding="utf-8")
            orphan.write_text("# Orphan\nIsolated.\n", encoding="utf-8")
            index.write_text("# Index\n- [[hub]]\n- [[dead]]\n- [[orphan]]\n", encoding="utf-8")
            cmd.write_text("# Command\n[[orphan]]\n", encoding="utf-8")

            files = [hub, dead, orphan, index, cmd]
            with mock.patch.object(lint_wiki, "VAULT", root), \
                 mock.patch.object(lint_wiki, "WIKI", wiki):
                graph = lint_wiki.link_graph(files)
                orphans = lint_wiki.orphan_pages(files, graph)
                deadends = lint_wiki.deadend_pages(files, graph)

            self.assertEqual(orphans, [orphan])
            self.assertEqual(deadends, [dead])


if __name__ == "__main__":
    unittest.main()
