"""Contradiction check on a small vault with a stub index and a stub model.

Checks which neighbours are asked about (not the page itself, not another
copy of the same document, not a summary of a renamed file, not below the
similarity floor), how a reply is parsed, and that CONTRADICTIONS.md keeps
each pair once, newest first, inside its window.

Run: python3 -m unittest tools.tests.test_contradiction_check -v
"""
import importlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))


class Index:
    def __init__(self, hits):
        self.hits = hits

    def query(self, text, k=25):
        return self.hits


class Check(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old = os.environ.get("BRAINLESS_VAULT")
        os.environ["BRAINLESS_VAULT"] = self.tmp.name
        self.addCleanup(lambda: os.environ.__setitem__("BRAINLESS_VAULT", self.old) if self.old
                        else os.environ.pop("BRAINLESS_VAULT", None))
        self.root = Path(self.tmp.name)
        import i18n
        import contradiction_check
        self.cc = importlib.reload(contradiction_check)
        i18n_t = i18n.t
        self.addCleanup(setattr, i18n, "t", i18n_t)
        i18n.t = lambda key, lang=None, **kw: i18n_t(key, lang="en", **kw)
        self.cc._ignored = lambda rels: set()
        self.summary("new", "Work/pricing.md")
        self.summary("note", "Work/product note.md")
        self.summary("new_raw_twin", "Work/pricing_raw.md")
        self.summary("renamed", "Work/old name.md", on_disk=False)
        self.write(".wiki/concepts/pricing.md", "# Pricing\n")
        self.write(".wiki/moc/Resources.md", "# MOC\n")

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def summary(self, stem, source, on_disk=True):
        if on_disk:
            self.write(source, "text\n")
        self.write(f".wiki/summaries/{stem}.md", f"---\nsummary_en: {stem}\nsource: {source}\n---\n# {stem}\n")

    def test_candidates_are_other_live_documents(self):
        s = ".wiki/summaries/"
        hits = [(0.99, s + "new.md", 0), (0.95, s + "note.md", 0), (0.91, s + "new_raw_twin.md", 0),
                (0.90, s + "renamed.md", 0), (0.89, ".wiki/moc/Resources.md", 0),
                (0.88, ".wiki/concepts/pricing.md", 0), (0.87, s + "note.md", 1), (0.80, s + "far.md", 0)]
        got = [o for _, o in self.cc.candidates(s + "new.md", hits)]
        # 0.95 is another copy of the same text; the twin is the same document;
        # the renamed file is gone; a MOC is not a claim-bearing page.
        self.assertEqual(got, [".wiki/concepts/pricing.md", s + "note.md"])

    def test_parse_keeps_known_pages_and_drops_noise(self):
        out = 'Sure: {"conflicts": [{"page": "a.md", "kind": "outdated", "new": "35 TL", "old": "50 TL", "why": "new price"},' \
              ' {"page": "zzz.md", "new": "x", "old": "y"}, {"page": "a.md", "new": "", "old": "y"}]}'
        self.assertEqual(self.cc.parse(out, {"a.md"}),
                         [{"page": "a.md", "kind": "outdated", "new": "35 TL", "old": "50 TL", "why": "new price"}])
        self.assertIsNone(self.cc.parse("no json here", {"a.md"}))
        self.assertEqual(self.cc.parse('{"conflicts": []}', {"a.md"}), [])

    def test_record_once_newest_first_inside_the_window(self):
        conflict = {"page": ".wiki/concepts/pricing.md", "kind": "contradiction",
                    "new": "35 TL", "old": "50 TL", "why": "two prices"}
        r = [{"page": ".wiki/summaries/new.md", "conflicts": [conflict]}]
        now = datetime(2026, 9, 25, 23, 0)
        self.assertEqual(len(self.cc.record(r, now=now)), 1)
        self.assertEqual(self.cc.record(r, now=now + timedelta(days=1)), [])   # same pair, not again
        text = self.cc.REPORT.read_text()
        self.assertEqual(text.count("35 TL"), 1)
        self.assertIn("[[new]] vs [[pricing]]", text)
        later = self.cc.record([], now=now + timedelta(days=self.cc.KEEP_DAYS + 1))
        self.assertEqual(later, [])
        self.assertNotIn("35 TL", self.cc.REPORT.read_text())                   # out of the window

    def test_night_asks_once_per_page_and_stops_at_the_deadline(self):
        prompts = []
        reply = json.dumps({"conflicts": [{"page": ".wiki/concepts/pricing.md", "kind": "contradiction",
                                           "new": "35", "old": "50", "why": "w"}]})
        idx = Index([(0.88, ".wiki/concepts/pricing.md", 0)])
        added, n = self.cc.run_night([".wiki/summaries/new.md", ".wiki/summaries/renamed.md"],
                                     run=lambda p: prompts.append(p) or reply, index=idx)
        self.assertEqual((n["checked"], n["conflicts"], len(prompts)), (1, 1, 1))  # renamed: skipped
        self.assertEqual(self.cc.brief_lines(added), [("new", "[[pricing]]: 35 / 50")])
        import time
        _, n = self.cc.run_night([".wiki/summaries/new.md"], run=lambda p: reply, index=idx,
                                 deadline=time.monotonic())
        self.assertEqual(n["checked"], 0)


if __name__ == "__main__":
    unittest.main()
