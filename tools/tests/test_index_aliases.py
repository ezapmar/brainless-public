"""INDEX with descriptions, topic indexes, and the alias layer.

The index is read before anything else, and aliases spread into frontmatter,
the index and search, so the tests check both for what they must never carry:
a dash, personal data, a stale topic file, or a search result that is an
index instead of a page.

Run: python3 -m unittest tools.tests.test_index_aliases -v
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
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import compile_resources as compiler  # noqa: E402
import concepts as C  # noqa: E402


def fm(**kw):
    return "---\n" + "\n".join(f"{k}: {v}" for k, v in kw.items()) + "\n---\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        wiki = self.root / ".wiki"
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        for name, value in (("VAULT", self.root), ("WIKI", wiki), ("INDEX_DIR", wiki / "_index"),
                            ("ALIAS_FILE", self.root / "_Agent-Context/aliases.md"),
                            ("ENTITY_REGISTRY", self.root / "_Agent-Context/entities.md"),
                            ("CONCEPT_REGISTRY", self.root / "_Agent-Context/concepts.md"),
                            ("CONCEPTS_DIR", wiki / "concepts"), ("COMPANY_AREA", "Work/Acme"),
                            ("_DEADLINE", None)):
            stack.enter_context(patch.object(compiler, name, value))
        self.answers = []
        stack.enter_context(patch.object(compiler, "call_claude",
                                         side_effect=lambda *a, **k: self.answers.pop(0) if self.answers else None))
        self.write("_Agent-Context/entities.md", "Nordhaven | company | Nordhaven Group | partnership\n")

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p


class Index(Base):
    def test_lines_carry_the_first_sentence_and_aliases_and_no_dash(self):
        self.write(".wiki/entities/Nordhaven.md", fm(lang="tr", summary_en=f"Buyer {chr(0x2014)} Nordic. Second sentence.",
                                                 aliases='["Nordhaven Group"]') + "# Nordhaven\n")
        line = compiler.index_line(self.root / ".wiki/entities/Nordhaven.md")
        self.assertEqual(line, "- [[Nordhaven]]: Buyer, Nordic. (aka Nordhaven Group)")

    def test_long_tail_goes_to_topic_indexes_and_stale_topics_disappear(self):
        self.write(".wiki/entities/Nordhaven.md", fm(lang="tr", summary_en="Buyer.") + "# Nordhaven\n")
        for i in range(3):
            self.write(f".wiki/summaries/lib-{i}.md", fm(lang="tr", summary_en=f"Book {i}.",
                                                         source=f"Library/Books/b{i}.md") + "x\n")
        self.write(".wiki/summaries/acme.md", fm(lang="tr", summary_en="Deal.",
                                                 source="Work/Acme/Deals/d.md") + "x\n")
        self.write(".wiki/_index/index-gone.md", "old\n")
        compiler.phase_index(False, False)
        index = (self.root / ".wiki/INDEX.md").read_text()
        self.assertIn("- [[Nordhaven]]: Buyer.", index)
        self.assertIn("[[index-summaries-library-books]]: Summaries, Library/Books (3 pages)", index)
        self.assertIn("Work/Acme/Deals", index)
        self.assertNotIn("lib-0", index)
        topic = (self.root / ".wiki/_index/index-summaries-library-books.md").read_text()
        self.assertIn("- [[lib-2]]: Book 2.", topic)
        self.assertFalse((self.root / ".wiki/_index/index-gone.md").exists())


class Aliases(Base):
    def test_rows_are_proposed_once_filtered_and_stamped(self):
        page = self.write(".wiki/entities/Nordhaven.md", fm(lang="tr", summary_en="Buyer.") + "# Nordhaven\n")
        self.answers.append(json.dumps({"Nordhaven": ["Nordhaven satın alma", "01.01.1970 doğumlu",
                                                  "tutar 3500 GBP", "ISO 27001 denetimi", "Nordhaven"]}))
        compiler.phase_aliases(False, False)
        rows = compiler.parse_alias_rows((self.root / "_Agent-Context/aliases.md").read_text())
        self.assertEqual(rows["Nordhaven"], ["Nordhaven satın alma", "ISO 27001 denetimi", "Nordhaven"])
        stamped = json.loads(C.fm_value(C.split_frontmatter(page.read_text())[0], "aliases"))
        self.assertEqual(stamped, ["Nordhaven satın alma", "ISO 27001 denetimi", "Nordhaven Group"])
        compiler.phase_aliases(False, False)       # no new pages: no call
        self.assertEqual(self.answers, [])

    def test_hand_edited_rows_win_and_are_still_filtered(self):
        page = self.write(".wiki/entities/Nordhaven.md", fm(lang="tr", summary_en="Buyer.") + "# Nordhaven\n")
        self.write("_Agent-Context/aliases.md", compiler.ALIAS_HEADER + "Nordhaven | Nordik alıcı, 12345678\n")
        compiler.phase_aliases(False, False)
        stamped = json.loads(C.fm_value(C.split_frontmatter(page.read_text())[0], "aliases"))
        self.assertEqual(stamped, ["Nordik alıcı", "Nordhaven Group"])


class SearchSkipsIndexes(unittest.TestCase):
    def test_indexes_never_come_back_as_results(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".wiki/_index").mkdir(parents=True)
            (root / ".wiki/concepts").mkdir()
            (root / ".wiki/INDEX.md").write_text("pricing pricing pricing")
            (root / ".wiki/_index/index-x.md").write_text("pricing pricing")
            (root / ".wiki/concepts/pricing.md").write_text("pricing tiers")
            old = os.environ.get("BRAINLESS_VAULT")
            os.environ["BRAINLESS_VAULT"] = d
            try:
                import wiki_search
                ws = importlib.reload(wiki_search)
                stems = [Path(doc["path"]).stem for _, doc in ws.search("pricing", k=5)]
            finally:
                if old is None:
                    os.environ.pop("BRAINLESS_VAULT", None)
                else:
                    os.environ["BRAINLESS_VAULT"] = old
                import wiki_search
                importlib.reload(wiki_search)
            self.assertEqual(stems, ["pricing"])


if __name__ == "__main__":
    unittest.main()
