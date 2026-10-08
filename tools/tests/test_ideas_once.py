"""The compiler writes an idea once.

What must hold: a proposed idea is skipped when a page with that slug exists,
when an existing page answers to its slug or title (file name, H1 or alias),
or when the slug sits in .wiki/_archive/ideas after a merge; a new idea is
written; the prompt names the ideas already held.

Run: python3 -B -m unittest tools.tests.test_ideas_once -v
"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]

import compile_resources as compiler


class IdeasOnce(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ideas = self.root / ".wiki/ideas"
        for d in (self.ideas, self.root / ".wiki/_archive/ideas", self.root / "Thinking/Beliefs"):
            d.mkdir(parents=True)
        (self.root / "Thinking/Beliefs/Commute.md").write_text("# Commute time compounds daily\n")
        self.kept = self.ideas / "commute-is-a-daily-tax.md"
        self.kept.write_text('---\nlang: en\nzk: 202609300007\naliases: ["Commute time is a tax"]\n---\n'
                             "# Commute is a daily tax\n\nThe merged page.\n")
        (self.root / ".wiki/_archive/ideas/commute-tax-old.md").write_text("# The commute, an old title\n")
        for p in (patch.object(compiler, "VAULT", self.root),
                  patch.object(compiler, "WIKI", self.root / ".wiki"),
                  patch.object(compiler, "out_of_time", return_value=False)):
            p.start()
            self.addCleanup(p.stop)
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def run_phase(self, ideas):
        with patch.object(compiler, "call_claude", return_value=json.dumps({"ideas": ideas})) as llm:
            compiler.phase_ideas(False, False)
        return llm.call_args[0][0]

    def test_an_idea_already_held_is_not_written_again(self):
        before = self.kept.read_text()
        prompt = self.run_phase([
            {"slug": "commute-is-a-daily-tax", "title": "Rewritten", "body": "x", "en": "x"},
            {"slug": "commute-tax-again", "title": "Commute time is a tax", "body": "x", "en": "x"},
            {"slug": "commute-tax-old", "title": "Yet another title", "body": "x", "en": "x"},
            {"slug": "commute-new-slug", "title": "The commute, an old title", "body": "x", "en": "x"},
            {"slug": "write-first", "title": "Write first", "body": "A new idea.", "en": "new"},
        ])
        self.assertEqual(self.kept.read_text(), before)
        self.assertEqual(sorted(p.name for p in self.ideas.glob("*.md")),
                         ["commute-is-a-daily-tax.md", "write-first.md"])
        self.assertIn("- commute-is-a-daily-tax", prompt)

    def test_two_proposals_of_one_new_idea_make_one_page(self):
        self.run_phase([
            {"slug": "write-first", "title": "Write first", "body": "a", "en": "a"},
            {"slug": "writing-comes-first", "title": "Write first", "body": "b", "en": "b"},
        ])
        self.assertEqual(len(list(self.ideas.glob("*.md"))), 2)


if __name__ == "__main__":
    unittest.main()
