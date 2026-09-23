"""Link suggestions on a small wiki with a stub embedder.

Pages about the same thing get the same axis; the tests check the mutual
nearest rule, that linked pairs are skipped, that raw twins go to
near-identical, and that the report adds no wikilinks.

Run: python3 -m unittest tools.tests.test_link_suggest -v
"""
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

try:
    import numpy as np
except ImportError:
    np = None

AXES = ["payroll", "garden", "sailing", "cinema", "tax"]


class Stub:
    def embed(self, texts, batch_size=32):
        for t in texts:
            low = t.lower()
            v = np.array([low.count(w) for w in AXES], dtype=np.float32) + 0.05
            yield v


@unittest.skipIf(np is None, "numpy not installed")
class LinkSuggest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old = os.environ.get("BRAINLESS_VAULT")
        os.environ["BRAINLESS_VAULT"] = self.tmp.name
        self.addCleanup(lambda: os.environ.__setitem__("BRAINLESS_VAULT", self.old) if self.old
                        else os.environ.pop("BRAINLESS_VAULT", None))
        self.root = Path(self.tmp.name)
        # Two payroll pages that do not link: the pair to find.
        self.page("summaries/work_payroll-meeting", "payroll payroll payroll run", source="Inbox/Spiky/a.md")
        self.page("projects/Payroll Integration", "payroll payroll project")
        # Two garden pages that already link: must not be proposed.
        self.page("summaries/personal_garden-notes", "garden garden garden [[Garden Plan]]")
        self.page("projects/Garden Plan", "garden garden plan")
        # A summary and its raw twin: near-identical, not a link.
        self.page("summaries/library_sailing-book", "sailing sailing sailing")
        self.page("summaries/library_sailing-book_raw", "sailing sailing sailing raw")
        # Filler so a percentile means something.
        for i, w in enumerate(["cinema", "tax", "cinema tax", "tax payroll garden"]):
            self.page(f"summaries/filler-{i}", w)
        import lint_wiki
        import semantic_index
        import link_suggest
        importlib.reload(lint_wiki)
        self.si = importlib.reload(semantic_index)
        self.si.Index._embedder = lambda _self: Stub()
        self.si.Index().build(self.root / ".wiki", log=lambda *_: None)
        self.ls = importlib.reload(link_suggest)

    def page(self, rel, body, source=None):
        p = self.root / ".wiki" / f"{rel}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        fm = f"source: {source}\n" if source else ""
        p.write_text(f"---\nlang: en\nsummary_en: {body.split('[[')[0]}\n{fm}---\n{body}\n")

    def pairs(self, res, key):
        return {frozenset((Path(r["a"]).stem, Path(r["b"]).stem)) for r in res[key]}

    def all_pairs(self, res):
        return self.pairs(res, "orphan_homes") | self.pairs(res, "new_links") | self.pairs(res, "near_identical")

    def test_finds_the_unlinked_pair(self):
        res = self.ls.suggest(k=2, percentile=50)
        self.assertIn(frozenset(("work_payroll-meeting", "Payroll Integration")), self.all_pairs(res))

    def test_skips_a_linked_pair(self):
        res = self.ls.suggest(k=2, percentile=50)
        self.assertNotIn(frozenset(("personal_garden-notes", "Garden Plan")), self.all_pairs(res))

    def test_raw_twin_is_near_identical(self):
        res = self.ls.suggest(k=2, percentile=50)
        self.assertIn(frozenset(("library_sailing-book", "library_sailing-book_raw")),
                      self.pairs(res, "near_identical"))

    def test_spiky_time_twin(self):
        self.assertTrue(self.ls.twins("a/inbox_spiky_2025-10-10-1458-demo.md", "a/inbox_spiky_2025-10-10-demo.md"))
        self.assertFalse(self.ls.twins("a/inbox_spiky_2025-10-10-demo.md", "a/inbox_spiky_2025-10-11-demo.md"))

    def test_report_has_no_wikilinks_and_leaves_the_graph(self):
        res = self.ls.suggest(k=2, percentile=50)
        text = self.ls.markdown(res)
        self.assertNotIn("[[", text)
        self.ls.REPORT.write_text(text)
        import lint_wiki
        self.assertNotIn(self.ls.REPORT, lint_wiki.all_wiki_files())

    def test_no_index_is_not_an_error(self):
        import shutil
        shutil.rmtree(self.root / ".agents")
        self.assertIsNone(self.ls.suggest())


if __name__ == "__main__":
    unittest.main()
