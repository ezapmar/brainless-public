"""Offline regressions for document retention, project compilation, and actions.

Run: python3 -m unittest discover -s tools/tests -v
"""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import build_dashboard as dashboard
import compile_resources as compiler

spec = importlib.util.spec_from_file_location(
    "smart_processor", ROOT / ".agents/scripts/smart_processor.py"
)
processor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(processor)


class FixtureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


class DocumentRetentionTests(FixtureTest):
    def setUp(self):
        super().setUp()
        self.source = self.write("Library/Books/book.pdf", "original bytes")
        self.work = self.source.with_suffix("")
        self.raw = self.work / "book_raw.md"
        self.summary = self.work / "Summary.md"
        self.fiche = self.work / "Fiche_de_Lecture.md"
        self.enterContext(patch.object(processor, "HIGH_VALUE_DIRS", [str(self.source.parent)]))
        self.enterContext(patch.object(processor, "SUMMARY_PROMPT", "summary|{out_path}"))
        self.enterContext(patch.object(processor, "FICHE_PROMPT", "fiche|{out_path}"))
        self.convert = self.enterContext(patch.object(
            processor, "convert_to_file", side_effect=lambda src, dst: Path(dst).write_text("raw content")
        ))
        self.llm = self.enterContext(patch.object(processor, "run_claude", side_effect=self.generate))
        self.git = self.enterContext(patch.object(processor.subprocess, "run"))

    @staticmethod
    def generate(prompt):
        kind, path = prompt.split("|")
        Path(path).write_text(f"# {kind}\nValid generated content")
        return True

    def process(self):
        return processor.process_file(str(self.source))

    def assert_sources_preserved(self):
        self.assertEqual(self.source.read_text(), "original bytes")
        self.assertEqual(self.raw.read_text(), "raw content")
        self.git.assert_not_called()

    def test_failed_llm_retains_source_and_raw(self):
        self.llm.side_effect = None
        self.llm.return_value = False
        self.assertEqual(self.process(), "failed")
        self.assert_sources_preserved()
        self.assertFalse(self.summary.exists())

    def test_success_without_output_is_failure(self):
        self.llm.side_effect = None
        self.llm.return_value = True
        self.assertEqual(self.process(), "failed")
        self.assert_sources_preserved()

    def test_empty_output_is_failure(self):
        def empty(prompt):
            Path(prompt.split("|")[1]).write_text(" \n")
            return True
        self.llm.side_effect = empty
        self.assertEqual(self.process(), "failed")
        self.assert_sources_preserved()
        self.assertFalse(self.summary.exists())

    def test_partial_failed_output_is_not_published(self):
        def partial(prompt):
            self.generate(prompt)
            return False
        self.llm.side_effect = partial
        self.assertEqual(self.process(), "failed")
        self.assert_sources_preserved()
        self.assertFalse(self.summary.exists())
        self.assertEqual(list(self.work.glob(".brainless-*")), [])

    def test_retry_only_regenerates_failed_fiche(self):
        self.llm.side_effect = lambda prompt: self.generate(prompt) if prompt.startswith("summary|") else False
        self.assertEqual(self.process(), "failed")
        self.assertTrue(self.summary.exists())
        self.llm.reset_mock(side_effect=True)
        self.llm.side_effect = self.generate
        self.assertEqual(self.process(), "converted")
        self.llm.assert_called_once()
        self.assertTrue(self.llm.call_args.args[0].startswith("fiche|"))
        self.assert_sources_preserved()

    def test_success_retains_sources_and_is_idempotent(self):
        self.assertEqual(self.process(), "converted")
        self.assert_sources_preserved()
        self.assertTrue(self.fiche.exists())
        self.llm.reset_mock()
        self.convert.reset_mock()
        self.assertEqual(self.process(), "skipped")
        self.llm.assert_not_called()
        self.convert.assert_not_called()

    def test_changed_source_refreshes_both_summaries(self):
        self.assertEqual(self.process(), "converted")
        for path in (self.raw, self.summary, self.fiche):
            os.utime(path, (1, 1))
        self.source.write_text("updated original")
        self.llm.reset_mock()
        self.convert.reset_mock()
        self.assertEqual(self.process(), "converted")
        self.assertEqual(self.llm.call_count, 2)
        self.convert.assert_called_once()
        self.assertEqual(self.source.read_text(), "updated original")

    def test_empty_raw_conversion_is_failure(self):
        self.convert.side_effect = lambda src, dst: Path(dst).write_text("")
        self.assertEqual(self.process(), "failed")
        self.assertTrue(self.source.exists())
        self.llm.assert_not_called()

    def test_general_document_retains_original_without_llm(self):
        with patch.object(processor, "HIGH_VALUE_DIRS", []):
            self.assertEqual(self.process(), "converted")
            self.assertEqual(self.process(), "skipped")
        self.assert_sources_preserved()
        self.llm.assert_not_called()


class ProjectCompilationTests(FixtureTest):
    def setUp(self):
        super().setUp()
        self.notes = self.write("Work/Example/notes.md", "Project overview")
        self.minutes = self.write("Work/Example/meeting.md", "Supporting evidence")
        self.output = self.root / ".wiki/projects/work/Example.md"
        self.enterContext(patch.object(compiler, "VAULT", self.root))
        self.enterContext(patch.object(compiler, "WIKI", self.root / ".wiki"))
        self.llm = self.enterContext(patch.object(
            compiler, "call_claude", return_value="---\nlang: en\n---\n# Compiled project"
        ))

    def compile(self, full=False):
        compiler.phase_projects(False, full)

    def test_private_descendants_never_reach_prompt_or_hash(self):
        private = [self.write(f"Work/Example/{folder}/private.md", "PRIVATE_SENTINEL")
                   for folder in ("Official Docs", "Security Incidents", "Example - Health")]
        self.compile()
        prompt = self.llm.call_args.args[0]
        self.assertIn("Supporting evidence", prompt)
        self.assertNotIn("PRIVATE_SENTINEL", prompt)
        digest = compiler.stored_digest(self.output)
        for path in private:
            path.write_text("CHANGED_PRIVATE_SENTINEL")
        self.llm.reset_mock()
        self.compile()
        self.llm.assert_not_called()
        self.assertEqual(compiler.stored_digest(self.output), digest)

    def test_private_project_is_skipped(self):
        self.write("Work/Official Docs/notes.md", "PRIVATE_PROJECT")
        self.compile()
        self.llm.assert_called_once()
        self.assertNotIn("PRIVATE_PROJECT", self.llm.call_args.args[0])

    def test_project_rebuilds_on_change_addition_rename_and_removal(self):
        self.compile()
        self.llm.reset_mock()
        self.compile()
        self.llm.assert_not_called()
        original_notes = self.notes.read_bytes()
        for change in ("edit", "add", "rename", "remove"):
            with self.subTest(change=change):
                old_hash = compiler.stored_digest(self.output)
                if change == "edit":
                    self.minutes.write_text("New supporting evidence")
                elif change == "add":
                    self.write("Work/Example/new.md", "Additional evidence")
                elif change == "rename":
                    self.minutes.rename(self.minutes.with_name("renamed.md"))
                else:
                    (self.notes.parent / "new.md").unlink()
                self.llm.reset_mock()
                self.compile()
                self.llm.assert_called_once()
                self.assertNotEqual(compiler.stored_digest(self.output), old_hash)
                self.assertEqual(self.notes.read_bytes(), original_notes)

    def test_legacy_notes_only_hash_is_migrated(self):
        self.minutes.unlink()
        compiler.write_compiled(self.output, "---\nlang: en\n---\nOld mirror", [self.notes])
        self.compile()
        self.llm.assert_called_once()
        self.llm.reset_mock()
        self.compile()
        self.llm.assert_not_called()

    def test_new_privacy_rule_invalidates_previous_mirror(self):
        self.compile()
        with patch.object(compiler, "PRIVATE_SEGMENTS", compiler.PRIVATE_SEGMENTS | {"meeting.md"}):
            self.llm.reset_mock()
            self.compile()
        self.llm.assert_called_once()
        self.assertNotIn("Supporting evidence", self.llm.call_args.args[0])

    def test_forced_rebuild_and_dry_run(self):
        self.compile()
        self.llm.reset_mock()
        compiler.phase_projects(True, True)
        self.llm.assert_not_called()
        self.compile(full=True)
        self.llm.assert_called_once()

    def test_failed_rebuild_preserves_existing_mirror_and_retries(self):
        self.compile()
        previous = self.output.read_bytes()
        self.minutes.write_text("Changed evidence")
        self.llm.return_value = None
        with contextlib.redirect_stderr(io.StringIO()):
            self.compile()
        self.assertEqual(self.output.read_bytes(), previous)
        self.llm.return_value = "---\nlang: en\n---\n# Updated"
        self.llm.reset_mock()
        self.compile()
        self.llm.assert_called_once()
        self.assertNotEqual(self.output.read_bytes(), previous)


class DashboardActionTests(unittest.TestCase):
    def test_kill_criteria_and_subheadings_are_not_actions(self):
        text = "## Kill Criteria\n- [ ] Stop condition\n### Details\n- [ ] More conditions\n## Actions\n- [ ] Interview a customer"
        self.assertEqual(dashboard.next_action(text), "Interview a customer")
        self.assertEqual(list(dashboard.open_tasks(text)), ["Interview a customer"])

    def test_explicit_next_action_plain_bullet_has_priority(self):
        text = "## Status\n- [ ] Secondary action\n## Kill Criteria\n- [ ] Stop condition\n## Next Action\n- Run the next research phase"
        self.assertEqual(dashboard.next_action(text), "Run the next research phase")

    def test_explicit_action_supports_prose_and_checkboxes(self):
        for body in ("Call the supplier", "- [ ] Call the supplier", "* Call the supplier"):
            with self.subTest(body=body):
                self.assertEqual(dashboard.next_action("## Next Action\n" + body), "Call the supplier")

    def test_comments_completed_and_struck_actions_are_skipped(self):
        text = "## Next Actions\n<!-- - [ ] Example -->\n- [x] Finished\n- [ ] ~~Cancelled~~\n- [ ] Live action"
        self.assertEqual(dashboard.next_action(text), "Live action")
        self.assertEqual(list(dashboard.open_tasks(text)), ["Live action"])

    def test_only_stop_conditions_yields_no_action(self):
        self.assertEqual(dashboard.next_action("## Kill Criteria\n- [ ] Stop condition"), "")

    def test_empty_template_action_does_not_become_a_task(self):
        template = (ROOT / "_Templates/Project.md").read_text()
        self.assertEqual(dashboard.next_action(template), "")
        self.assertEqual(dashboard.next_action("## Next Action\n- [ ]\n## Status\n- [ ] Real task"), "Real task")


if __name__ == "__main__":
    unittest.main()
