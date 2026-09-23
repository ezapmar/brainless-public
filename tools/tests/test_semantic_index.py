"""Semantic index and hybrid search, with a stub embedder in place of a model.

The stub maps text to a bag of hand-picked concept axes, so "medical cover"
and "health insurance" land together the way a real multilingual model puts
them, without downloading one.

Run: python3 -m unittest tools.tests.test_semantic_index -v
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
except ImportError:  # the addon is optional; so is this test
    np = None

AXES = [("insurance", "cover", "sigorta"), ("hiring", "ilan", "engineer"), ("garden", "tomato")]


class StubEmbedder:
    def __init__(self):
        self.calls = 0

    def embed(self, texts, batch_size=32):
        for t in texts:
            self.calls += 1
            low = t.lower()
            yield np.array([1.0 + sum(low.count(w) for w in ax) * 5 for ax in AXES], dtype=np.float32) - 1.0 + 0.01


@unittest.skipIf(np is None, "numpy not installed")
class Semantic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old = os.environ.get("BRAINLESS_VAULT")
        os.environ["BRAINLESS_VAULT"] = self.tmp.name
        self.addCleanup(lambda: os.environ.__setitem__("BRAINLESS_VAULT", self.old) if self.old
                        else os.environ.pop("BRAINLESS_VAULT", None))
        self.root = Path(self.tmp.name)
        self.page("insurance-memo", "Company pays private health insurance for the family.")
        self.page("fde-posting", "Job posting: forward deployed engineer, hiring now.")
        self.page("garden", "Tomatoes need sun and water.")
        import wiki_search
        import semantic_index
        self.ws = importlib.reload(wiki_search)
        self.si = importlib.reload(semantic_index)
        self.stub = StubEmbedder()
        self.si.Index._embedder = lambda _self: self.stub

    def page(self, name, body, summary="x"):
        p = self.root / ".wiki" / "summaries" / f"{name}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"---\nlang: en\nsummary_en: {summary}\n---\n# {name}\n\n{body}\n")

    def test_passages_lead_with_title_and_summary(self):
        ps = self.si.passages("---\nsummary_en: A memo.\n---\n" + "word " * 600, "memo")
        self.assertTrue(ps[0].startswith("memo. A memo.\n"))
        self.assertGreater(len(ps), 1)

    def test_query_finds_meaning_not_words(self):
        idx = self.si.Index("stub")
        idx.build(self.root / ".wiki", log=lambda *_: None)
        top = idx.query("medical cover", k=3)[0]
        self.assertEqual(Path(top[1]).stem, "insurance-memo")

    def test_rebuild_embeds_only_changed_pages(self):
        idx = self.si.Index("stub")
        idx.build(self.root / ".wiki", log=lambda *_: None)
        first = self.stub.calls
        self.page("garden", "Tomatoes need sun, water and a cage.")
        res = self.si.Index("stub").build(self.root / ".wiki", log=lambda *_: None)
        self.assertEqual(res["embedded"], self.stub.calls - first)
        self.assertEqual(res["embedded"], 1)
        self.assertEqual(res["pages"], 3)

    def test_deleted_page_leaves_index(self):
        self.si.Index("stub").build(self.root / ".wiki", log=lambda *_: None)
        (self.root / ".wiki" / "summaries" / "garden.md").unlink()
        idx = self.si.Index("stub")
        idx.build(self.root / ".wiki", log=lambda *_: None)
        self.assertNotIn("garden", {Path(r).stem for r, _ in idx.meta["rows"]})

    def test_hybrid_lifts_the_page_bm25_misses(self):
        idx = self.si.Index("stub")
        idx.build(self.root / ".wiki", log=lambda *_: None)
        self.ws._INDEX = idx
        os.environ["BRAINLESS_SEARCH"] = "hybrid"
        self.addCleanup(os.environ.pop, "BRAINLESS_SEARCH", None)
        stems = [Path(d["path"]).stem for _, d in self.ws.search("medical cover sigorta", k=3)]
        self.assertEqual(stems[0], "insurance-memo")

    def test_no_index_means_bm25(self):
        self.ws._INDEX = None
        self.assertEqual(self.ws.mode(), "bm25")


if __name__ == "__main__":
    unittest.main()
