"""Offline regressions for the dialectic scorecard.

The scorecard is computed, not argued: affirmation rate, unanimity warning,
who moved and whether they cited evidence, and the two rolling flags
(sycophancy, a persona that never votes NO). These tests pin that arithmetic
against hand-written replies. No model, no relay.

Run: python3 -B -m unittest discover -s tools/tests -v
"""
import contextlib
from datetime import datetime, timedelta
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("BRAINLESS_VAULT", str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import dialectic  # noqa: E402

NAMES = [name for _, name in dialectic.PERSONAS]


def reply(vote, number=None, evidence=None, bold=False):
    v = f"**Vote:** {vote}" if bold else f"Vote: {vote}"
    lines = ["Finding: something.", "Strongest objection: something else.", v]
    if number is not None:
        lines.append(f"**Number:** {number}%" if bold else f"Number: {number}%")
    if evidence is not None:
        lines.append(f"New evidence: {evidence}")
    return "\n".join(lines)


def rounds(r1_votes, r2_votes=None):
    """Build r1/r2 dicts from lists of (vote, number[, evidence]) per persona."""
    r1 = {n: reply(*v) for n, v in zip(NAMES, r1_votes)}
    r2 = {n: reply(*v) for n, v in zip(NAMES, r2_votes)} if r2_votes else {n: dialectic.NO_REPLY for n in NAMES}
    return r1, r2


class ParseReplyTest(unittest.TestCase):
    def test_plain_and_bold_vote_lines_parse_the_same(self):
        for bold in (False, True):
            p = dialectic.parse_reply(reply("CONDITIONAL", 65, bold=bold))
            self.assertEqual((p["vote"], p["number"]), ("CONDITIONAL", 65))

    def test_vote_is_case_insensitive_and_number_is_clamped(self):
        p = dialectic.parse_reply("vote: yes\nnumber: 140%")
        self.assertEqual((p["vote"], p["number"]), ("YES", 100))

    def test_no_reply_and_missing_vote_are_distinct(self):
        self.assertFalse(dialectic.parse_reply(dialectic.NO_REPLY)["replied"])
        self.assertFalse(dialectic.parse_reply("")["replied"])
        p = dialectic.parse_reply("I have thoughts but no vote line.")
        self.assertTrue(p["replied"])
        self.assertIsNone(p["vote"])

    def test_pass_is_an_abstention(self):
        self.assertEqual(dialectic.parse_reply("Pass: not a strategy question.")["vote"], "ABSTAIN")

    def test_new_evidence_none_variants_read_as_no_evidence(self):
        for none in ("none", "None.", "no new evidence", "nothing new", "n/a", "N/A"):
            p = dialectic.parse_reply(reply("NO", 30, evidence=none))
            self.assertIsNone(p["evidence"], none)
        p = dialectic.parse_reply(reply("NO", 30, evidence="The Q2 churn table in the board pack."))
        self.assertTrue(p["evidence"].startswith("The Q2 churn"))


class ScoreTopicTest(unittest.TestCase):
    def test_affirmation_counts_round_one_yes_over_votes_cast(self):
        r1, r2 = rounds([("YES", 70), ("YES", 60), ("NO", 20), ("CONDITIONAL", 50), ("ABSTAIN",)])
        s = dialectic.score_topic(r1, r2)
        self.assertEqual((s["yes"], s["voted"]), (2, 4))
        self.assertAlmostEqual(s["affirm"], 0.5)

    def test_abstentions_and_unparsed_replies_are_not_votes(self):
        r1, r2 = rounds([("YES", 70), ("ABSTAIN",), ("YES", 60), ("YES", 55), ("NO", 40)])
        r1["Skeptic"] = "A reply with no vote line at all."
        s = dialectic.score_topic(r1, r2)
        self.assertEqual(s["voted"], 3)
        self.assertEqual(s["unparsed"], 1)

    def test_unanimity_needs_three_identical_votes(self):
        r1, r2 = rounds([("YES", 70), ("YES", 60), ("YES", 80), ("ABSTAIN",), ("ABSTAIN",)])
        s = dialectic.score_topic(r1, r2)
        self.assertTrue(s["unanimous"])
        self.assertEqual(s["unanimous_vote"], "YES")
        r1, r2 = rounds([("NO", 20), ("NO", 25), ("ABSTAIN",), ("ABSTAIN",), ("ABSTAIN",)])
        self.assertFalse(dialectic.score_topic(r1, r2)["unanimous"], "two votes are not a consensus")
        r1, r2 = rounds([("YES", 70), ("YES", 60), ("YES", 80), ("CONDITIONAL", 50), ("YES", 90)])
        self.assertFalse(dialectic.score_topic(r1, r2)["unanimous"])

    def test_moved_means_vote_changed_or_fifteen_points(self):
        r1, r2 = rounds(
            [("YES", 70), ("YES", 70), ("YES", 70), ("YES", 70), ("NO", 30)],
            [("NO", 70), ("YES", 55), ("YES", 56), ("YES", 85), ("NO", 30)],
        )
        rows = {r["persona"]: r["moved"] for r in dialectic.score_topic(r1, r2)["rows"]}
        self.assertTrue(rows["Skeptic"], "vote flipped")
        self.assertTrue(rows["Gambler"], "fifteen points down")
        self.assertFalse(rows["Scientist"], "fourteen points is not a move")
        self.assertTrue(rows["Postmortem"], "fifteen points up")
        self.assertFalse(rows["Strategist"], "unchanged")

    def test_moved_without_evidence_is_counted_separately(self):
        r1, r2 = rounds(
            [("YES", 70), ("YES", 70), ("NO", 30), ("NO", 30), ("NO", 30)],
            [("NO", 70, "none"), ("NO", 70, "a churn table nobody had read"), ("NO", 30), ("NO", 30), ("NO", 30)],
        )
        s = dialectic.score_topic(r1, r2)
        self.assertEqual((s["moved"], s["moved_with_evidence"], s["moved_without_evidence"]), (2, 1, 1))

    def test_missing_round_two_reply_is_not_a_move(self):
        r1, r2 = rounds([("YES", 70)] * 5, [("NO", 20)] * 5)
        r2["Gambler"] = dialectic.NO_REPLY
        s = dialectic.score_topic(r1, r2)
        self.assertEqual(s["both"], 4)
        self.assertIsNone(next(r["moved"] for r in s["rows"] if r["persona"] == "Gambler"))
        self.assertEqual(s["moved"], 4)

    def test_rendered_scorecard_warns_only_when_unanimous_and_has_no_dashes(self):
        r1, r2 = rounds([("YES", 70)] * 5, [("YES", 70)] * 5)
        md = dialectic.render_scorecard(dialectic.score_topic(r1, r2))
        self.assertIn("Unanimity warning", md)
        self.assertIn("5/5 (100%)", md)
        self.assertNotRegex(md, "[\\u2013\\u2014]")   # em and en dashes are banned in every write
        r1, r2 = rounds([("YES", 70), ("NO", 30), ("YES", 60), ("CONDITIONAL", 50), ("NO", 20)])
        self.assertNotIn("Unanimity warning", dialectic.render_scorecard(dialectic.score_topic(r1, r2)))


class RollingScorecardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        state = Path(self.tmp.name)
        self.scores = state / "dialectic_scores.jsonl"
        self.enterContext(patch.object(dialectic, "STATE_DIR", str(state)))
        self.enterContext(patch.object(dialectic, "SCORES_FILE", str(self.scores)))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def record(self, days_ago=0, yes=3, voted=5, votes=None, unanimous=False):
        date = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        rec = {"date": date, "mode": "noon", "title": "t", "kind": "adhoc", "yes": yes, "voted": voted,
               "unanimous": unanimous, "moved": 0, "moved_with_evidence": 0, "both": voted,
               "votes": votes or {}}
        with open(self.scores, "a") as fh:
            fh.write(json.dumps(rec) + "\n")

    def test_empty_history_renders_a_placeholder(self):
        self.assertIn("No scored rounds yet", dialectic.scorecard_markdown())

    def test_window_drops_records_older_than_thirty_days(self):
        self.record(days_ago=31, yes=5, voted=5)
        self.record(days_ago=1, yes=1, voted=5)
        self.assertEqual(len(dialectic.load_scores()), 1)
        self.assertIn("Affirmation: 1/5 (20%)", dialectic.scorecard_markdown())

    def test_sycophancy_flag_needs_more_than_sixty_percent_and_ten_votes(self):
        self.record(yes=4, voted=5)                      # 80% but only 5 votes
        self.assertNotIn("Sycophancy flag", dialectic.scorecard_markdown())
        self.record(yes=4, voted=5)                      # 8/10 = 80%
        self.assertIn("Sycophancy flag", dialectic.scorecard_markdown())

    def test_exactly_sixty_percent_is_not_a_flag(self):
        self.record(yes=3, voted=5)
        self.record(yes=3, voted=5)
        self.assertNotIn("Sycophancy flag", dialectic.scorecard_markdown())

    def test_persona_that_never_votes_no_is_flagged_after_five_votes(self):
        for _ in range(4):
            self.record(votes={"Gambler": "YES", "Skeptic": "NO"})
        self.assertNotIn("Gambler has not voted NO", dialectic.scorecard_markdown())
        self.record(votes={"Gambler": "CONDITIONAL", "Skeptic": "NO"})
        md = dialectic.scorecard_markdown()
        self.assertIn("Gambler has not voted NO in 5 votes", md)
        self.assertNotIn("Skeptic has not voted NO", md)

    def test_abstentions_do_not_count_towards_the_never_no_flag(self):
        for _ in range(5):
            self.record(votes={"Strategist": "ABSTAIN"})
        self.assertNotIn("Strategist has not voted NO", dialectic.scorecard_markdown())

    def test_unanimity_rate_uses_topics_with_three_or_more_votes(self):
        self.record(voted=2, unanimous=False)
        self.record(voted=5, unanimous=True)
        self.assertIn("Unanimous round 1: 1/1 (100%)", dialectic.scorecard_markdown())

    def test_append_score_classifies_the_topic_kind(self):
        score = dialectic.score_topic(*rounds([("YES", 70)] * 5))
        cases = [({"title": "a", "sources": []}, "adhoc"),
                 ({"title": "b", "sources": ["x.md"]}, "capture"),
                 ({"title": "c", "sources": [], "fallback_key": "q"}, "vault")]
        for topic, kind in cases:
            self.assertEqual(dialectic.append_score("2026-09-18", "noon", topic, score)["kind"], kind)
        self.assertEqual(len(self.scores.read_text().splitlines()), 3)


if __name__ == "__main__":
    unittest.main()


class TwoPageRenderTest(unittest.TestCase):
    """The filed note is two pages and a folded appendix: verdict and fields
    first, method trace second, the raw rounds under a collapsed callout."""

    def persona_reply(self, vote, number, finding, objection):
        return (f"**3. Ambiguous:** a word.\n\n**Finding:** {finding}\n\n"
                f"**Strongest objection:** {objection}\n\n**Question for the owner:** why?\n\n"
                f"**Vote:** {vote}\n**Number:** {number}%")

    def round_two(self, vote, number, chosen, evidence="none"):
        return (f"**Chosen objection:** {chosen}\n\n**My answer:** fine.\n\n**Vote:** {vote}\n\n"
                f"**Number:** {number}%\n\n**New evidence:** {evidence}")

    SYNTH = ("### Synthesis\n**Conclusion:** Settled little.\n**Strongest counterargument:** Skeptic's.\n"
             "**Bet:** 40%\n### Method trace\n**Starting question:** Is X true?\n**Hypotheses tested:** H1.\n"
             "### Proposal\nNone")

    def test_verdict_follows_the_final_majority(self):
        r1, r2 = rounds([("YES", 70)] * 5, [("YES", 70), ("YES", 60), ("NO", 20), ("YES", 80), ("CONDITIONAL", 50)])
        self.assertEqual(dialectic.verdict(dialectic.score_topic(r1, r2))[0], "Go")
        r1, r2 = rounds([("YES", 70)] * 5, [("NO", 20), ("NO", 25), ("NO", 30), ("YES", 80), ("CONDITIONAL", 50)])
        self.assertEqual(dialectic.verdict(dialectic.score_topic(r1, r2))[0], "Stop")
        r1, r2 = rounds([("YES", 70)] * 5, [("YES", 20), ("YES", 25), ("NO", 30), ("NO", 80), ("CONDITIONAL", 50)])
        call, median, _ = dialectic.verdict(dialectic.score_topic(r1, r2))
        self.assertEqual((call, median), ("Test first", 30))
        r1, r2 = rounds([("YES", 70)] * 5)      # nobody answered round 2: round 1 decides
        self.assertEqual(dialectic.verdict(dialectic.score_topic(r1, r2))[0], "Go")
        self.assertEqual(dialectic.verdict(dialectic.score_topic({}, {}))[0], "Test first")

    def test_field_reads_same_line_or_next_line_and_drops_pipes(self):
        txt = self.persona_reply("NO", 30, "The claim rests on one | source.", "Nobody measured it.")
        self.assertEqual(dialectic._field(txt, "finding"), "The claim rests on one / source.")
        self.assertEqual(dialectic._field(txt, "strongest objection"), "Nobody measured it.")
        self.assertEqual(dialectic._field("**Finding**\n\nOn the next line.\n", "finding"), "On the next line.")
        self.assertEqual(dialectic._field("no such label", "finding"), "")
        self.assertEqual(dialectic._field(dialectic.NO_REPLY, "finding"), "")

    def test_split_synthesis_keeps_every_block(self):
        parts = dialectic.split_synthesis(self.SYNTH)
        self.assertIn("**Conclusion:** Settled little.", parts["synthesis"])
        self.assertIn("Is X true?", parts["method trace"])
        self.assertEqual(parts["proposal"], "None")
        self.assertEqual(dialectic.split_synthesis("free text")["synthesis"], "free text")

    def test_render_topic_is_two_pages_and_a_folded_transcript(self):
        r1 = {n: self.persona_reply("CONDITIONAL", 50, f"{n} finding.", f"{n} objection.") for n in NAMES}
        r2 = {n: self.round_two("CONDITIONAL", 55, "Skeptic's.") for n in NAMES}
        r2["Gambler"] = dialectic.NO_REPLY
        score = dialectic.score_topic(r1, r2)
        md = dialectic.render_topic({"title": "T", "claim": "C", "sources": []}, r1, r2, self.SYNTH, score=score)
        order = [md.index(h) for h in ("### Decision summary", "**Verdict:** Test first", "**Conclusion:**",
                                       "| Persona | Round 1 | Round 2 | Moved |", "### Proposal", "### Method trace",
                                       "**Starting question:**", "| Persona | Finding | Strongest objection | Vote |",
                                       "> [!note]- Full transcript", "> **Round 1**", "> **Round 2**")]
        self.assertEqual(order, sorted(order), "sections out of order")
        self.assertIn("| Skeptic | Skeptic finding. | Skeptic objection. | CONDITIONAL 50% |", md)
        self.assertIn(f"| Gambler | {dialectic.NO_REPLY} | | |", md)
        self.assertIn("> [!warning] Unanimity warning", md)
        self.assertNotIn("<span", md)
        self.assertNotRegex(md, "[\\u2013\\u2014]")
        # every transcript line is inside the callout
        after = md.split("> [!note]- Full transcript", 1)[1]
        self.assertTrue(all(l.startswith(">") for l in after.strip().splitlines()), "transcript leaked out of the fold")


class NightStatusTest(unittest.TestCase):
    def test_night_lines_live_in_their_own_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "DIALECTIC-STATUS.md")
            with patch.object(dialectic, "STATUS_MD", path):
                dialectic.write_status("2026-09-19", "evening", "ok", "1 topics")
                dialectic.write_status("2026-09-20", "night", "ok", "usable, 9/12 replies passed")
                dialectic.write_status("2026-09-20", "noon", "idle", "no new captures")
                dialectic.write_status("2026-09-20", "night", "ok", "usable, 10/12 replies passed")
                text = open(path).read()
        day_lines = [l for l in text.splitlines() if l.startswith("- 20")]
        night_lines = [l for l in text.splitlines() if l.startswith("- night ")]
        self.assertEqual(len(day_lines), 2)
        self.assertEqual(night_lines, ["- night 2026-09-20: ok, usable, 10/12 replies passed"], "night line replaced, not duplicated")
        self.assertLess(text.index(day_lines[-1]), text.index("## Night experiment"), "day lines stay first for the checks that read the last one")

    def test_verdict_line_for_night_judgement(self):
        md = dialectic.render_judgement({"Skeptic": {"r1": True, "r2": False}, "Gambler": {"r1": True}}, 2, 3)
        self.assertIn("**Local replies usable:** 2/3", md)
        self.assertIn("| Skeptic | usable | unusable |", md)
        self.assertIn(f"| Gambler | usable | {dialectic.NO_REPLY} |", md)
