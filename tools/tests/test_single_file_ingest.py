"""Tests for single-file compilation (--file), post-ship feedback prompts, and Telegram locale strings."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cloud_gate
import compile_resources as cr
import i18n


class SingleFileIngestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name)
        wiki = self.vault / ".wiki"
        for d in ("Library", "Work/Demo", "Personal/Private", "_Agent-Context", ".wiki/summaries", ".wiki/concepts"):
            (self.vault / d).mkdir(parents=True, exist_ok=True)
        (self.vault / "_Agent-Context/PROFILE.md").write_text(
            "---\n"
            "owner_name: Test\n"
            "output_lang: en\n"
            "company_area: Work/Company\n"
            "cloud_exclude: Personal/Private\n"
            "---\n",
            encoding="utf-8",
        )
        (self.vault / "_Agent-Context/concepts.md").write_text(
            "# Concept registry\n\n"
            "cash-discipline | Cash Discipline | cash | active |  | cash flow rules\n",
            encoding="utf-8",
        )

        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
        for name, value in (
            ("VAULT", self.vault),
            ("WIKI", wiki),
            ("COMPANY_AREA", "Work/Company"),
            ("CONCEPT_REGISTRY", self.vault / "_Agent-Context/concepts.md"),
            ("CONCEPTS_DIR", wiki / "concepts"),
            ("CONCEPT_ARCHIVE", wiki / "_archive/articles"),
            ("INDEX_DIR", wiki / "_index"),
            ("OVERVIEW_PAGE", wiki / "overview.md"),
            ("_DEADLINE", None),
            ("_RETRACTED", set()),
            ("_COUNTS", {}),
            ("LANG", "en"),
        ):
            stack.enter_context(patch.object(cr, name, value))
        stack.enter_context(patch.object(cr, "t", side_effect=lambda k, **kw: i18n.t(k, lang="en", **kw)))
        stack.enter_context(patch.object(cloud_gate, "VAULT", self.vault))
        stack.enter_context(patch.object(cloud_gate, "CLOUD_EXCLUDE", ("Personal/Private",)))

    def test_rejects_outside_vault_missing_and_non_markdown(self):
        bad_outside = cr.compile_single_file("/etc/hosts", dry=True)
        self.assertEqual(bad_outside["status"], "error")

        bad_missing = cr.compile_single_file("Library/missing.md", dry=True)
        self.assertEqual(bad_missing["status"], "error")

        pdf = self.vault / "Library/doc.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        bad_ext = cr.compile_single_file("Library/doc.pdf", dry=True)
        self.assertEqual(bad_ext["status"], "error")

    def test_skips_secret_source_without_calling_model(self):
        secret = self.vault / "Library/secret-note.md"
        secret.write_text("---\nprivacy: secret\n---\n# Secret\nDo not compile.\n", encoding="utf-8")
        with patch.object(cr, "call_claude") as mocked:
            res = cr.compile_single_file("Library/secret-note.md", dry=False)
        self.assertEqual(res["status"], "skipped")
        self.assertEqual(res["reason"], "private")
        mocked.assert_not_called()

    def test_cloud_exclude_routes_to_compile_local_lane(self):
        priv = self.vault / "Personal/Private/note.md"
        priv.write_text("# Private note\nDetails stay on Mac.\n", encoding="utf-8")
        res = cr.compile_single_file("Personal/Private/note.md", dry=True)
        self.assertEqual(res["status"], "dry-run")
        self.assertEqual(res["lane"], "compile-local")

    def test_single_file_compiles_summary_concepts_and_index(self):
        src = self.vault / "Library/cash-lesson.md"
        src.write_text(
            "# Cash Lesson\n\n"
            "## Outcomes & Feedback\n"
            "- 2026-11-24 | Q4 pricing | 3 customers accepted annual prepay | annual prepay beats discounts\n",
            encoding="utf-8",
        )
        prompts_seen = []

        def fake_claude(prompt, timeout=300, *, concept=False, lane=None, sources=None):
            prompts_seen.append((prompt, lane))
            if "Produce a concise wiki summary" in prompt:
                return (
                    "---\n"
                    "lang: en\n"
                    "summary_en: Observed annual prepay feedback beats discounting.\n"
                    "source: Library/cash-lesson.md\n"
                    "compiled_at: 2026-11-24T12:00:00\n"
                    "status: seed\n"
                    "---\n"
                    "# cash-lesson\n\n"
                    "## Summary\nObserved outcome from Q4 pricing.\n\n"
                    "## Key Points\n- Observed: 3 customers accepted annual prepay.\n\n"
                    "## Links\n\n"
                    "## Cross-effects (if any)\nNone.\n"
                )
            if "Output ONLY JSON" in prompt:
                stem = cr._summary_dst(src).stem
                return json.dumps({"assign": {stem: ["cash-discipline"]}, "proposals": []})
            if "You maintain one concept page" in prompt:
                stem = cr._summary_dst(src).stem
                return (
                    "---\n"
                    "lang: en\n"
                    "summary_en: Cash discipline grounded in prepay evidence.\n"
                    "type: concept\n"
                    "aliases: [\"cash\"]\n"
                    "compiled_at: 2026-11-24T12:00:01\n"
                    "status: seed\n"
                    "---\n"
                    "# Cash Discipline\n\n"
                    "## Explanation\nCash comes first.\n\n"
                    "## Current Position\n- Annual prepay beats discounting [[%s]] (2026-11-24, primary).\n\n"
                    "## Contested\n(none yet)\n\n"
                    "## Superseded\n(none yet)\n\n"
                    "## Open Questions\n- None.\n\n"
                    "## Related\n- [[%s]]\n\n"
                    "## Cross-effects (if any)\nNone.\n"
                ) % (stem, stem)
            return ""

        with patch.object(cr, "call_claude", side_effect=fake_claude):
            res = cr.compile_single_file("Library/cash-lesson.md", dry=False)

        self.assertEqual(res["status"], "ok")
        self.assertTrue(res["summary_changed"])
        self.assertEqual(res["assigned"], ["cash-discipline"])
        self.assertEqual(res["concepts_updated"], ["cash-discipline"])
        self.assertTrue((self.vault / res["summary"]).exists())
        self.assertTrue((self.vault / ".wiki/concepts/cash-discipline.md").exists())
        self.assertTrue((self.vault / ".wiki/INDEX.md").exists())
        self.assertTrue(any("post-ship outcomes or external feedback" in p for p, _ in prompts_seen))
        self.assertTrue(any("Weight observed post-ship outcomes" in p for p, _ in prompts_seen))

    def test_project_mirror_prompt_includes_feedback_section(self):
        notes = self.vault / "Work/Demo/notes.md"
        notes.write_text("# Demo\n\n## Outcomes & Feedback\n- 2026-11-24 | pilot | passed | ship it\n", encoding="utf-8")
        seen = []
        with patch.object(cr, "call_claude", side_effect=lambda p, **kw: (seen.append(p), "")[1]):
            cr.phase_projects(dry=False, full=True)
        self.assertEqual(len(seen), 1)
        self.assertIn("## Outcomes & Feedback", seen[0])
        self.assertIn("Weight observed outcomes above untested plans", seen[0])

    def test_telegram_locales_have_no_buzz_or_banned_dashes(self):
        banned = ("\u2014", "\u2013")
        for lang in ("en", "tr"):
            waiting = i18n.t("concept_review.waiting_line", lang=lang, label="X", days=3)
            self.assertIn("Telegram #dreaming", waiting)
            self.assertNotIn("Buzz", waiting)
            seed_src = i18n.t("thinking_loop.seed_source", lang=lang)
            self.assertIn("Telegram", seed_src)
            self.assertNotIn("Buzz", seed_src)
            for key in (
                "morning_briefing.h_attention",
                "morning_briefing.attention_quiet",
                "morning_briefing.attention_unstated",
                "compile_resources.feedback_heading",
            ):
                val = i18n.t(key, lang=lang)
                self.assertTrue(val)
                for ch in banned:
                    self.assertNotIn(ch, val)


if __name__ == "__main__":
    unittest.main()
