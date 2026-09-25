"""The nightly change brief on a small wiki in a temp directory.

Checks added/updated/removed per kind, new links, new Contested and Superseded
bullets on concept pages, and that unchanged pages and placeholders stay quiet.

Run: python3 -m unittest tools.tests.test_wiki_changes -v
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import i18n  # noqa: E402
import wiki_changes as wc  # noqa: E402

# English strings whatever the owner's language, without touching the profile.
wc.t = lambda key, **kw: i18n.t(key, lang="en", **kw)

BANNED = (chr(0x2014), chr(0x2013))
CONCEPT = """---
type: concept
---
# Cash Discipline

## Current Position
- Cash is real, profit is judgement [[book-a]]

## Contested
{contested}

## Superseded
{superseded}
"""


def put(base: Path, rel: str, text: str):
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class WikiChangesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.wiki = Path(self.tmp.name)
        put(self.wiki, "summaries/old.md", "# Old\n\nsee [[a]]\n")
        put(self.wiki, "summaries/gone.md", "# Gone\n")
        put(self.wiki, "summaries/same.md", "# Same\n")
        put(self.wiki, "concepts/cash.md",
            CONCEPT.format(contested="- When to take profit [[book-a]]", superseded="(none yet)"))
        put(self.wiki, "_lint-report.md", "noise\n")
        self.before = wc.snapshot(self.wiki)

    def tearDown(self):
        self.tmp.cleanup()

    def test_changes_links_and_flags(self):
        put(self.wiki, "summaries/old.md", "# Old\n\nsee [[a]] and [[b]] and [[c]]\n")
        (self.wiki / "summaries/gone.md").unlink()
        put(self.wiki, "summaries/new.md", "# New Source\n")
        put(self.wiki, "concepts/cash.md", CONCEPT.format(
            contested="- When to take profit [[book-a]]\n- Is LER the same base as Real Revenue",
            superseded="- ~~Revenue is the scoreboard~~ replaced by margin"))
        put(self.wiki, "_lint-report.md", "more noise\n")
        d = wc.diff(self.before, wc.snapshot(self.wiki))

        self.assertEqual(d["added"], {"summaries": ["New Source"]})
        self.assertEqual(d["removed"], {"summaries": ["Gone"]})
        self.assertEqual(sorted(d["updated"]), ["concepts", "summaries"])
        self.assertEqual(d["updated"]["summaries"], ["Old"])
        self.assertEqual((d["links"], d["linked_pages"]), (2, 1))
        self.assertEqual(d["contested"], [("Cash Discipline", "Is LER the same base as Real Revenue")])
        self.assertEqual(d["superseded"],
                         [("Cash Discipline", "~~Revenue is the scoreboard~~ replaced by margin")])

        md = wc.markdown(d)
        self.assertIn("Summaries: 1 new (New Source)", md)
        self.assertIn("Contested, Cash Discipline: Is LER", md)
        for ch in BANNED:
            self.assertNotIn(ch, md)

    def test_quiet_night(self):
        d = wc.diff(self.before, wc.snapshot(self.wiki))
        self.assertTrue(wc.is_empty(d))
        self.assertIn("changed nothing", wc.markdown(d))

    def test_long_lists_are_capped(self):
        for i in range(wc.MAX_LISTED + 3):
            put(self.wiki, f"summaries/n{i}.md", f"# N{i}\n")
        md = wc.markdown(wc.diff(self.before, wc.snapshot(self.wiki)))
        self.assertIn("and 3 more", md)


if __name__ == "__main__":
    unittest.main()
