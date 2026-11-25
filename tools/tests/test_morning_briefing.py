"""morning_briefing: the block is built from the files other jobs wrote and lands once at the top.

What must hold: a rerun replaces the block instead of stacking one; the evening
close-out below it survives; a stale compile brief is said, not shown; only
promises older than two days are listed, freshest first.

Run: python3 -B -m unittest tools.tests.test_morning_briefing -v
"""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import morning_briefing as mb

NOW = datetime(2026, 11, 19, 7, 30)


class Morning(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        old = mb.CTX
        mb.CTX = Path(self.tmp.name)
        self.addCleanup(setattr, mb, "CTX", old)
        self.addCleanup(setattr, mb, "calendar", mb.calendar)
        mb.calendar = lambda now: ["(calendar)"]

    def write(self, name, text):
        (mb.CTX / name).write_text(text, encoding="utf-8")

    def test_section_reads_one_heading_and_stops_at_the_next(self):
        text = "# T\n\n## Alpha (3)\n- a\n\n- b\n## Beta\n- c\n"
        self.assertEqual(mb.section(text, "## Alpha ({n})"), ["- a", "- b"])
        self.assertEqual(mb.section(text, "## Gamma"), [])

    def test_a_rerun_replaces_the_block_and_keeps_the_evening_half(self):
        evening = "---\ndate: 2026-11-19\n---\n# Note\n\n## Akşam Kapanışı\n- done\n"
        once = mb.merge(evening, f"{mb.START}\nfirst\n{mb.END}")
        self.assertTrue(once.startswith("---\ndate: 2026-11-19\n---\n" + mb.START))
        twice = mb.merge(once, f"{mb.START}\nsecond\n{mb.END}")
        self.assertEqual(twice.count(mb.START), 1)
        self.assertIn("second", twice)
        self.assertNotIn("first", twice)
        self.assertIn("## Akşam Kapanışı\n- done", twice)

    def test_an_empty_day_gets_just_the_block(self):
        self.assertEqual(mb.merge("", "B"), "B\n")

    def test_only_promises_older_than_two_days_freshest_first(self):
        head = mb.t("nightly_processor.tasks_section")
        self.write("TASKS.md", f"# Ledger\n\n{head}\n\n"
                   "- [ ] old one | [[m]] | 2026-08-07\n"
                   "- [x] ticked | [[m]] | 2026-08-07\n"
                   "- [ ] recent one | [[m]] | 2026-11-16\n"
                   "- [ ] too fresh | [[m]] | 2026-11-18\n"
                   "\n## Other\n- [ ] not a promise | 2026-01-01\n")
        got = mb.promises(NOW)
        self.assertEqual([ln.split("|")[0].strip() for ln in got], ["- [ ] recent one", "- [ ] old one"])

    def test_a_stale_compile_brief_is_named_not_shown(self):
        self.write("WIKI-CHANGES.md", "# W\n\n" + mb.t("wiki_changes.updated_line", time="2026-11-16 23:00")
                   + "\n\n" + mb.t("wiki_changes.changed_heading") + "\n- something\n")
        lines, note = mb.wiki(NOW)
        self.assertEqual(lines, [])
        self.assertIn("2026-11-16 23:00", note)

    def test_a_red_engine_leads_the_block_and_the_actions(self):
        self.write("HEALTH.md", "# Health\n\n**Motor: 🔴 RED** (x)\n\n- 🔴 Night shift: failed\n- 🟢 Git: ok\n\n> footer\n")
        block = mb.build(NOW)
        body = block.split(mb.t("morning_briefing.h_health"))[1]
        self.assertTrue(body.strip().startswith(mb.t("morning_briefing.red_flag")))
        self.assertIn("1. 🔴 Night shift: failed", block)
        self.assertNotIn("> footer", block)
        self.assertTrue(block.startswith(mb.START) and block.endswith(mb.END))

    def test_attention_gaps_and_unlisted_folders_appear_when_fresh(self):
        unlisted = "\n".join(f"- `Work/Extra {i}`: {i + 1} edited files (not listed in active projects)" for i in range(6))
        self.write(
            "ATTENTION.md",
            "# Attention Audit (Stated vs Inferred)\n\n"
            "> Generated automatically by `tools/attention_audit.py`. Window: last 7 days.\n"
            "> status_checked: 2026-11-19 06:30\n\n"
            f"## Inferred Activity Outside Stated Projects (6)\n{unlisted}\n\n"
            "## Attention Gaps: Stated Priorities With Zero Touches (1)\n"
            "- [[Second Brain System]] [Personal]: 0 touches in 7 days (last log 2026-10-08)\n",
        )
        block = mb.build(NOW)
        self.assertIn(mb.t("morning_briefing.h_attention"), block)
        self.assertIn(mb.t("morning_briefing.attention_quiet"), block)
        self.assertIn("[[Second Brain System]]", block)
        self.assertIn(mb.t("morning_briefing.attention_unstated"), block)
        self.assertIn("Work/Extra 0", block)
        self.assertNotIn("Work/Extra 5", block)
        self.assertIn("_Agent-Context/ATTENTION.md", block)

    def test_attention_section_is_silent_when_clean_or_stale(self):
        self.write(
            "ATTENTION.md",
            "# Attention Audit (Stated vs Inferred)\n\n"
            "> status_checked: 2026-11-19 06:30\n\n"
            "## Inferred Activity Outside Stated Projects (0)\n- None.\n\n"
            "## Attention Gaps: Stated Priorities With Zero Touches (0)\n"
            "- Every stated active project saw activity in this window.\n",
        )
        self.assertEqual(mb.attention(NOW), [])
        self.write(
            "ATTENTION.md",
            "# Attention Audit (Stated vs Inferred)\n\n"
            "> status_checked: 2026-11-16 06:30\n\n"
            "## Attention Gaps: Stated Priorities With Zero Touches (1)\n"
            "- [[Old]] [Work]: 0 touches in 7 days\n",
        )
        self.assertEqual(mb.attention(NOW), [])


if __name__ == "__main__":
    unittest.main()
