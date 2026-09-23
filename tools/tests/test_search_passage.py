"""Passage-level sources: search says where in the page the answer is.

Run: python3 -m unittest tools.tests.test_search_passage -v
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import wiki_search  # noqa: E402

FM = "---\nlang: tr\nsummary_en: A page.\n---\n"


def doc(body, name="page"):
    return {"path": f"/v/.wiki/summaries/{name}.md", "text": FM + body}


class Passage(unittest.TestCase):
    def test_snippet_folds_turkish_letters(self):
        # "maas" in the query must find "maaş" in the text, not fall back to the frontmatter.
        r = wiki_search.passage(doc("Giriş.\n\nMaaş pazarlığında sakin kal."), "maas pazarligi")
        self.assertIn("Maaş pazarlığında", r["snippet"])
        self.assertNotIn("summary_en", r["snippet"])
        self.assertEqual(r["line"], 7)

    def test_line_points_at_the_match_deep_in_a_long_page(self):
        filler = "\n".join(f"line {i} about something else entirely here" for i in range(120))
        d = doc(filler + "\nThe garanti integration scope was agreed in April with the bank.\n" + filler)
        r = wiki_search.passage(d, "garanti integration scope")
        want = d["text"].split("\n").index(
            "The garanti integration scope was agreed in April with the bank.") + 1
        self.assertEqual(r["line"], want)
        self.assertIn("garanti integration scope", r["snippet"])
        self.assertGreater(r["passage_no"], 0)

    def test_the_embedding_choice_wins(self):
        d = doc("alpha " * 400 + "beta " * 400)
        d["passage_no"] = 0
        self.assertEqual(wiki_search.passage(d, "beta")["passage_no"], 0)

    def test_timestamp_before_the_match(self):
        d = doc("[00:10] intro talk\n\n[12:30] now the pricing tiers are explained in detail here\n")
        r = wiki_search.passage(d, "pricing tiers explained")
        self.assertEqual(r["at"], "12:30")

    def test_no_timestamp_is_none(self):
        self.assertIsNone(wiki_search.passage(doc("plain text about pricing tiers and more words"),
                                              "pricing")["at"])


if __name__ == "__main__":
    unittest.main()
