"""The nightly digest's deterministic edges, on a throwaway vault.

The digest itself is one model call; what surrounds it is not. process_notes
sizes each capture by its label and must keep a model failure from becoming an
empty digest; classify_today and weight_block read note_classify.py's state
file, where the last line wins; _clean_task makes a ledger row that the Google
Tasks sync can parse. run_prompt is replaced by a fixed reply.

Run: python3 -B -m unittest discover -s tools/tests -v
"""
import atexit
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]

_IMPORT_VAULT = tempfile.mkdtemp(prefix="brainless-nightly-")
atexit.register(shutil.rmtree, _IMPORT_VAULT, True)
_OLD_VAULT = os.environ.get("BRAINLESS_VAULT")
os.environ["BRAINLESS_VAULT"] = _IMPORT_VAULT
try:
    import nightly_processor as np
finally:
    if _OLD_VAULT is None:
        os.environ.pop("BRAINLESS_VAULT", None)
    else:
        os.environ["BRAINLESS_VAULT"] = _OLD_VAULT

DAY = "2026-10-08"


class VaultFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name)
        self.daily = self.vault / "Thinking" / "Daily"
        self.daily.mkdir(parents=True)
        self.tags = self.vault / ".agents" / "state" / "note_tags.jsonl"
        for name, value in (("VAULT_ROOT", str(self.vault)), ("CAPTURE_DIR", str(self.daily)),
                            ("PROJECTS_WORK_DIR", str(self.vault / "Work")),
                            ("PROJECTS_PERSONAL_DIR", str(self.vault / "Personal")),
                            ("NOTE_TAGS", str(self.tags))):
            patcher = patch.object(np, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.out = io.StringIO()
        redirect = contextlib.redirect_stdout(self.out)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    def note(self, name, text):
        p = self.daily / name
        p.write_text(text)
        return str(p)

    def tag_lines(self, *records, raw=()):
        self.tags.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(r) for r in records] + list(raw)
        self.tags.write_text("\n".join(lines) + "\n")


class ProcessNotes(VaultFixture):
    def run_digest(self, files, labels=None, reply="  # Digest\n\nBody.  \n"):
        self.llm = Mock(return_value=reply) if not isinstance(reply, Exception) else Mock(side_effect=reply)
        with patch.object(np, "run_prompt", self.llm):
            return np.process_notes(files, labels)

    def prompt(self):
        return self.llm.call_args.args[0]

    def test_reply_is_stripped_and_the_call_uses_the_nightly_lane(self):
        out = self.run_digest([self.note("a.md", "hello")])
        self.assertEqual(out, "# Digest\n\nBody.")
        self.assertEqual(self.llm.call_args.kwargs, {"timeout": 600, "lane": "nightly"})

    def test_each_source_carries_its_label_and_unlabelled_means_story(self):
        files = [self.note("a.md", "alpha"), self.note("b.md", "beta")]
        self.run_digest(files, {"a.md": "task"})
        self.assertIn("--- Source: a.md [task] ---\nalpha\n", self.prompt())
        self.assertIn("--- Source: b.md [story] ---\nbeta\n", self.prompt())

    def test_caps_per_label_and_in_total(self):
        story = self.note("s.md", "s" * (np.PER_FILE_CAP + 50))
        epic = self.note("e.md", "e" * (np.EPIC_FILE_CAP + 50))
        self.run_digest([story, epic], {"e.md": "epic"})
        prompt = self.prompt()
        self.assertIn("s" * np.PER_FILE_CAP + "\n", prompt)
        self.assertNotIn("s" * (np.PER_FILE_CAP + 1), prompt)
        self.assertIn("e" * np.EPIC_FILE_CAP + "\n", prompt)
        self.assertNotIn("e" * (np.EPIC_FILE_CAP + 1), prompt)
        many = [self.note(f"n{i}.md", "x" * np.PER_FILE_CAP) for i in range(8)]
        self.run_digest(many)
        self.assertLessEqual(self.prompt().count("x"), np.TOTAL_CAP)
        self.assertNotIn("Source: n7.md", self.prompt(), "the total cap cuts the tail")

    def test_known_projects_are_folders_with_notes(self):
        (self.vault / "Work" / "Alpha").mkdir(parents=True)
        (self.vault / "Work" / "Alpha" / "notes.md").write_text("x")
        (self.vault / "Work" / "Docs").mkdir()
        (self.vault / "Personal" / "Garden").mkdir(parents=True)
        (self.vault / "Personal" / "Garden" / "notes.md").write_text("x")
        self.run_digest([self.note("a.md", "hello")])
        self.assertIn("[[Alpha]] (work)", self.prompt())
        self.assertIn("[[Garden]] (personal)", self.prompt())
        self.assertNotIn("[[Docs]]", self.prompt())

    def test_an_unreadable_capture_is_skipped_not_fatal(self):
        out = self.run_digest([str(self.daily / "gone.md"), self.note("a.md", "hello")])
        self.assertEqual(out, "# Digest\n\nBody.")
        self.assertIn("Error reading", self.out.getvalue())
        self.assertNotIn("Source: gone.md", self.prompt())

    def test_model_failure_is_none_not_an_empty_digest(self):
        self.assertIsNone(self.run_digest([self.note("a.md", "x")], reply=None))
        self.assertIsNone(self.run_digest([self.note("a.md", "x")], reply=""))
        self.assertIsNone(self.run_digest([self.note("a.md", "x")], reply=RuntimeError("down")))
        self.assertIn("Exception: down", self.out.getvalue())


class ClassifyToday(VaultFixture):
    def classify(self, notes, run=None):
        self.run = run or Mock()
        with patch.object(np.subprocess, "run", self.run):
            return np.classify_today(notes)

    def test_runs_the_classifier_for_one_day_first(self):
        self.classify([])
        cmd = self.run.call_args.args[0]
        self.assertEqual(cmd[1:], [os.path.join(str(self.vault), "tools/note_classify.py"), "--days", "1"])
        self.assertEqual(self.run.call_args.kwargs["cwd"], str(self.vault))
        self.assertFalse(self.run.call_args.kwargs["check"])

    def test_labels_by_file_name_last_line_wins(self):
        a, b, c = (self.note(n, "x") for n in ("a.md", "b.md", "c.md"))
        self.tag_lines({"path": "Thinking/Daily/a.md", "label": "task"},
                       {"path": "Thinking/Daily/b.md"},
                       {"path": "Thinking/Daily/a.md", "label": "epic"},
                       {"path": "Thinking/Daily/other.md", "label": "story"},
                       raw=["not json", ""])
        self.assertEqual(self.classify([a, b, c]), {"a.md": "epic", "b.md": "task"})

    def test_a_failing_classifier_or_missing_state_still_returns(self):
        a = self.note("a.md", "x")
        self.assertEqual(self.classify([a], run=Mock(side_effect=OSError("no python"))), {})
        self.assertIn("note_classify skipped", self.out.getvalue())
        self.tag_lines({"path": "Thinking/Daily/a.md", "label": "story"})
        self.assertEqual(self.classify([a], run=Mock(side_effect=OSError("no python"))), {"a.md": "story"})


class WeightBlock(VaultFixture):
    def test_nothing_classified_leaves_the_digest_unchanged(self):
        self.assertEqual(np.weight_block(DAY), "")
        self.tag_lines({"date": "2026-10-07", "path": "Thinking/Daily/a.md", "label": "task"})
        self.assertEqual(np.weight_block(DAY), "")

    def test_one_line_per_note_sorted_last_line_wins(self):
        self.tag_lines({"date": DAY, "path": "Thinking/Daily/b.md", "label": "story", "score": 2, "method": "rules"},
                       {"date": DAY, "path": "Thinking/Daily/a.md", "label": "task", "score": 1, "method": "rules"},
                       {"date": DAY, "path": "Thinking/Daily/b.md", "label": "task", "score": 3, "method": "llm"},
                       {"date": DAY, "path": "Thinking/Daily/c.md"},
                       raw=["{broken"])
        heading = "## " + np.t("nightly_processor.weight_heading")
        self.assertEqual(np.weight_block(DAY), "\n".join([
            "", heading, "",
            "- `Thinking/Daily/a.md` #type/task (score 1, rules)",
            "- `Thinking/Daily/b.md` #type/task (score 3, llm)",
            "- `Thinking/Daily/c.md` #type/task (score None, None)",
        ]) + "\n")

    def test_an_epic_adds_the_research_note(self):
        self.tag_lines({"date": DAY, "path": "Thinking/Daily/a.md", "label": "epic", "score": 9, "method": "llm"})
        block = np.weight_block(DAY)
        self.assertIn("#type/epic", block)
        self.assertTrue(block.endswith("\n\n" + np.t("nightly_processor.weight_epic_note") + "\n"))


class CleanTask(unittest.TestCase):
    def test_ledger_safe_single_line(self):
        raw = "Call the bank #work/finance about [[Acme|Acme Ltd]] | today (from 2026-10-01)\nthen\tfile it."
        self.assertEqual(np._clean_task(raw), "Call the bank about (Acme/Acme Ltd) / today then file it")

    def test_tags_need_a_space_before_them(self):
        self.assertEqual(np._clean_task("#urgent fix the roof #home"), "#urgent fix the roof")
        self.assertEqual(np._clean_task("issue#12 stays"), "issue#12 stays")

    def test_trailing_dots_and_the_limit(self):
        self.assertEqual(np._clean_task("Done..."), "Done")
        self.assertEqual(np._clean_task("  a   b  "), "a b")
        self.assertEqual(np._clean_task("y" * 200), "y" * 160)
        self.assertEqual(np._clean_task("y" * 50, limit=10), "y" * 10)
        self.assertEqual(np._clean_task(" #only-a-tag"), "")

    def test_a_ticket_number_is_not_a_tag(self):
        self.assertEqual(np._clean_task("Chase support ticket #4802366"), "Chase support ticket #4802366")


if __name__ == "__main__":
    unittest.main()
