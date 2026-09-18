"""Offline regressions for calibrate.scan(): the three buckets that drive grading.

  DUE TO GRADE      review date passed, Outcome still pending
  NEEDS PREDICTION  decided, but no confidence or prediction written
  NO REVIEW DATE    pending or deferred with no review date

Run: python3 -B -m unittest discover -s tools/tests -v
"""
from datetime import date, timedelta
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import calibrate  # noqa: E402

PAST = (date.today() - timedelta(days=10)).isoformat()
FUTURE = (date.today() + timedelta(days=10)).isoformat()
PENDING = "## Outcome\n_Pending review._\n"
GRADED = "## Outcome\nIt held. Conversion rose 6%.\n"
TEMPLATE_PREDICTION = "## Prediction\n- **Prediction:** <!-- a falsifiable statement -->\n- **Confidence:** %\n"
REAL_PREDICTION = "## Prediction\n- **Prediction:** The landlord accepts a 3 year term.\n- **Confidence:** 70%\n"


class ScanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dec = Path(self.tmp.name) / "Thinking" / "Decisions"
        self.dec.mkdir(parents=True)
        self.enterContext(patch.object(calibrate, "DEC", self.dec))

    def note(self, name, meta, body=""):
        fm = "\n".join(f"{k}: {v}" for k, v in meta.items())
        (self.dec / f"{name}.md").write_text(f"---\n{fm}\n---\n# {name}\n\n{body}", encoding="utf-8")

    def scan(self):
        due, needs, no_review = calibrate.scan()
        return [n for n, _ in due], needs, no_review

    # --- DUE TO GRADE ----------------------------------------------------------

    def test_passed_review_with_pending_outcome_is_due(self):
        self.note("Lease", {"status": "decided", "review": PAST, "confidence": "70%"}, PENDING)
        due, _, _ = self.scan()
        self.assertEqual(due, ["Lease"])
        self.assertEqual(calibrate.scan()[0][0][1].isoformat(), PAST, "the review date travels with the item")

    def test_review_today_counts_as_passed(self):
        self.note("Today", {"status": "decided", "review": date.today().isoformat(), "confidence": "60%"}, PENDING)
        self.assertEqual(self.scan()[0], ["Today"])

    def test_future_review_is_not_due(self):
        self.note("Later", {"status": "decided", "review": FUTURE, "confidence": "70%"}, PENDING)
        self.assertEqual(self.scan()[0], [])

    def test_written_outcome_is_not_due(self):
        self.note("Done", {"status": "decided", "review": PAST, "confidence": "70%"}, GRADED)
        self.assertEqual(self.scan()[0], [])

    def test_graded_frontmatter_date_is_not_due_even_with_pending_text(self):
        self.note("Marked", {"status": "decided", "review": PAST, "graded": PAST, "confidence": "70%"}, PENDING)
        self.assertEqual(self.scan()[0], [])

    def test_revisit_is_a_fallback_for_review(self):
        self.note("Old", {"status": "decided", "revisit": PAST, "confidence": "70%"}, PENDING)
        self.assertEqual(self.scan()[0], ["Old"])

    def test_due_items_are_sorted_by_name(self):
        for name in ("Zeta", "Alpha", "Mid"):
            self.note(name, {"status": "decided", "review": PAST, "confidence": "70%"}, PENDING)
        self.assertEqual(self.scan()[0], ["Alpha", "Mid", "Zeta"])

    # --- NEEDS PREDICTION ---------------------------------------------------

    def test_decided_without_confidence_or_prediction_needs_one(self):
        self.note("Bare", {"status": "decided", "review": FUTURE}, PENDING)
        self.assertEqual(self.scan()[1], ["Bare"])

    def test_template_placeholder_is_not_a_prediction(self):
        self.note("Blank", {"status": "decided", "review": FUTURE}, TEMPLATE_PREDICTION + PENDING)
        self.assertEqual(self.scan()[1], ["Blank"])

    def test_written_prediction_or_confidence_satisfies(self):
        self.note("Text", {"status": "decided", "review": FUTURE}, REAL_PREDICTION + PENDING)
        self.note("Meta", {"status": "decided", "review": FUTURE, "confidence": "65%"}, PENDING)
        self.assertEqual(self.scan()[1], [])

    def test_only_decided_notes_need_a_prediction(self):
        self.note("Pending", {"status": "pending", "review": FUTURE}, PENDING)
        self.note("Deferred", {"status": "deferred", "review": FUTURE}, PENDING)
        self.assertEqual(self.scan()[1], [])

    def test_status_is_case_insensitive(self):
        self.note("Loud", {"status": "DECIDED", "review": FUTURE}, PENDING)
        self.assertEqual(self.scan()[1], ["Loud"])

    # --- NO REVIEW DATE -----------------------------------------------------

    def test_pending_and_deferred_without_review_are_listed(self):
        self.note("P", {"status": "pending"}, PENDING)
        self.note("D", {"status": "deferred"}, PENDING)
        self.note("Dec", {"status": "decided", "confidence": "70%"}, PENDING)
        self.assertEqual(sorted(self.scan()[2]), ["D", "P"])

    def test_blank_and_malformed_dates_count_as_missing(self):
        self.note("Blank", {"status": "pending", "review": ""}, PENDING)
        self.note("Words", {"status": "pending", "review": "next quarter"}, PENDING)
        self.assertEqual(sorted(self.scan()[2]), ["Blank", "Words"])

    def test_trailing_comment_on_a_date_is_ignored(self):
        self.note("Commented", {"status": "pending", "review": f"{PAST}  # grade after the board"}, PENDING)
        due, _, no_review = self.scan()
        self.assertEqual((due, no_review), (["Commented"], []))

    # --- edges ------------------------------------------------------------------

    def test_one_note_can_sit_in_two_buckets(self):
        self.note("Both", {"status": "decided", "review": PAST}, PENDING)
        due, needs, _ = self.scan()
        self.assertEqual((due, needs), (["Both"], ["Both"]))

    def test_missing_decisions_folder_is_empty_not_an_error(self):
        with patch.object(calibrate, "DEC", Path(self.tmp.name) / "nope"):
            self.assertEqual(calibrate.scan(), ([], [], []))

    def test_note_without_frontmatter_is_ignored_quietly(self):
        (self.dec / "Loose.md").write_text("# Loose\nNo frontmatter at all.\n")
        self.assertEqual(self.scan(), ([], [], []))


if __name__ == "__main__":
    unittest.main()
