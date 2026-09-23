"""The retrieval eval, on a three-page wiki where the right answer is known.

Run: python3 -m unittest tools.tests.test_retrieval_eval -v
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


class Eval(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old = os.environ.get("BRAINLESS_VAULT")
        os.environ["BRAINLESS_VAULT"] = self.tmp.name
        self.addCleanup(lambda: os.environ.__setitem__("BRAINLESS_VAULT", self.old) if self.old
                        else os.environ.pop("BRAINLESS_VAULT", None))
        root = Path(self.tmp.name)
        for name, body in (("pricing", "value based pricing for payroll software tiers"),
                           ("hiring", "hiring plan for engineers and interview loop"),
                           ("garden", "tomatoes need sun and water")):
            p = root / ".wiki" / "concepts" / f"{name}.md"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"---\nlang: en\nsummary_en: x\n---\n# {name}\n\n{body}\n")
        (root / "_Agent-Context").mkdir()
        (root / "_Agent-Context" / "retrieval-golden.json").write_text(json.dumps([
            {"q": "how should we price the payroll tiers", "expect": ["pricing"]},
            {"q": "interview loop for engineers", "expect": ["hiring"]},
            {"q": "quantum chromodynamics", "expect": ["physics"]},
        ]))
        import wiki_search
        import retrieval_eval
        importlib.reload(wiki_search)
        self.re = importlib.reload(retrieval_eval)

    def test_scores_count_hits_and_misses(self):
        res = self.re.evaluate()
        self.assertEqual((res["n"], res["hit1"], res["hit5"]), (3, 67, 67))
        self.assertIsNone(res["rows"][2]["rank"])

    def test_changes_name_the_question_that_moved(self):
        prev = {"rows": [{"q": "a", "rank": 1}, {"q": "b", "rank": 2}]}
        cur = {"rows": [{"q": "a", "rank": None}, {"q": "b", "rank": 1}]}
        moved = self.re.changes(prev, cur)
        self.assertTrue(moved[0].startswith("worse: a"))
        self.assertTrue(moved[1].startswith("better: b"))

    def test_rank_of_takes_the_first_expected_page(self):
        self.assertEqual(self.re.rank_of(["x", "y"], ["a", "y", "x"]), 2)
        self.assertIsNone(self.re.rank_of(["x"], ["a", "b"]))


class Regression(unittest.TestCase):
    def test_a_fifteen_point_drop_is_named(self):
        import health_check
        now = datetime(2026, 9, 22, 12)
        roll = [{"day": (now - timedelta(days=n)).strftime("%Y-%m-%d"), "job": "nightly_processor",
                 "runs": 1, "fails": 0, "last": "23:30", "status": "ok",
                 "counts": {"captures": 2, "hit5": score}} for n, score in ((3, 82), (2, 80), (1, 64))]
        found = [m for _, m in health_check.run_findings(roll, now=now)]
        self.assertTrue(any("64" in m and "82" in m for m in found), found)


if __name__ == "__main__":
    unittest.main()
