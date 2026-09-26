"""Retracting a source on a small vault with a stub model.

Checks the trace (twin and book folder included, archived pages ignored), that
apply records the document, archives its summaries, keeps a concept page the
validator refuses and names it for a hand edit, unlinks other pages, and that
the compiler then skips the document.

Run: python3 -m unittest tools.tests.test_retract_source -v
"""
import importlib
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

CONCEPT = """---
lang: en
summary_en: Cash discipline.
type: concept
members: {{"bad": "h1", "good": "h2"}}
---
# Cash

## Current Position
- Profit comes first [[bad]]
- Cash is real [[good]] [[bad]]

## Superseded
- ~~old claim~~ superseded 2026-01: why ([[good]])
{extra}
## Related
- [[bad]]
- [[good]]
"""

GOOD_REWRITE = """---
lang: en
summary_en: Cash discipline.
type: concept
members: {"bad": "h1", "good": "h2"}
---
# Cash

## Current Position
- Cash is real [[good]]

## Superseded
- ~~old claim~~ superseded 2026-01: why ([[good]])
- ~~Profit comes first~~ retracted 2026-09: source withdrawn (bad data)

## Related
- [[good]]
"""


class Retract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old = os.environ.get("BRAINLESS_VAULT")
        os.environ["BRAINLESS_VAULT"] = self.tmp.name
        self.addCleanup(lambda: os.environ.__setitem__("BRAINLESS_VAULT", self.old) if self.old
                        else os.environ.pop("BRAINLESS_VAULT", None))
        self.root = Path(self.tmp.name)
        import compile_resources
        import retract_source
        self.cr = importlib.reload(compile_resources)
        self.rs = importlib.reload(retract_source)
        self.write("Library/bad.md", "text")
        self.write("Library/bad_raw.md", "text")
        self.write("Library/good.md", "text")
        for stem, src in (("bad", "Library/bad.md"), ("bad_raw", "Library/bad_raw.md"), ("good", "Library/good.md")):
            self.write(f".wiki/summaries/{stem}.md", f"---\nsummary_en: {stem}\nsource: {src}\n---\n# {stem}\n")
        self.concept = self.write(".wiki/concepts/cash.md", CONCEPT.format(extra=""))
        self.moc = self.write(".wiki/moc/Resources.md", "# MOC\n- [[bad|Profit First]]\n- [[good]]\n")
        self.write(".wiki/_archive/summaries/bad.md", "stale copy [[bad]]\n")
        self.write(".wiki/_archive/LOG.md", "# Log\n\n| a | b | c | d | e |\n|---|---|---|---|---|\n")

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def test_trace_finds_every_copy_and_every_page(self):
        tr = self.rs.trace("Library/bad.md")
        self.assertEqual({p.name for p in tr["summaries"]}, {"bad.md", "bad_raw.md"})
        self.assertEqual([p.name for p, _ in tr["concepts"]], ["cash.md"])
        self.assertEqual([p.name for p, _ in tr["others"]], ["Resources.md"])

    def test_apply_takes_it_out(self):
        tr = self.rs.trace("Library/bad.md")
        done = self.rs.apply(tr, "bad data", run=lambda p: GOOD_REWRITE, now=datetime(2026, 9, 25))
        self.assertEqual(done["concepts"], [".wiki/concepts/cash.md"])
        self.assertEqual(done["hand_edit"], [])
        page = self.concept.read_text()
        self.assertNotIn("[[bad]]", page)
        self.assertIn("retracted 2026-09", page)
        self.assertNotIn('"bad"', page.split("---")[1])            # gone from members
        self.assertEqual(self.moc.read_text(), "# MOC\n- Profit First\n- [[good]]\n")
        self.assertFalse((self.root / ".wiki/summaries/bad_raw.md").exists())
        self.assertTrue((self.root / ".wiki/_archive/summaries/bad_raw.md").exists())
        self.assertIn("| retract |", (self.root / ".wiki/_archive/LOG.md").read_text())
        self.assertEqual([r["source"] for r in self.rs.rows()], ["Library/bad.md"])
        # The compiler now skips the document, twin included.
        self.cr._RETRACTED = None
        self.assertEqual(self.cr.source_skip(self.root / "Library/bad_raw.md"), "retracted")
        self.assertIsNone(self.cr.source_skip(self.root / "Library/good.md"))
        self.assertEqual([s["stem"] for s in self.cr.summary_catalogue()], ["good"])

    def test_a_rewrite_that_loses_other_history_is_refused(self):
        lossy = GOOD_REWRITE.replace("- ~~old claim~~ superseded 2026-01: why ([[good]])\n", "")
        tr = self.rs.trace("Library/bad.md")
        before = self.concept.read_text()
        done = self.rs.apply(tr, "bad data", run=lambda p: lossy)
        self.assertEqual(self.concept.read_text(), before)
        self.assertEqual(done["hand_edit"][0][0], ".wiki/concepts/cash.md")
        self.assertIn("superseded", done["hand_edit"][0][1])
        # The summaries stay while a page still cites them; the registry row is already in.
        self.assertTrue((self.root / ".wiki/summaries/bad.md").exists())
        self.assertEqual(done["archived"], 0)
        self.assertEqual(len(self.rs.rows()), 1)
        # A second run after the fix finishes, and does not add the row twice.
        done = self.rs.apply(self.rs.trace("Library/bad.md"), "bad data", run=lambda p: GOOD_REWRITE)
        self.assertEqual((done["archived"], len(self.rs.rows())), (2, 1))


if __name__ == "__main__":
    unittest.main()
