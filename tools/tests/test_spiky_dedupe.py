"""The ledger guard that does not rely on the model behaving.

The extraction prompt tells the model not to repeat a task already in the
ledger. A large model obeys; a small local one does not always, and the cost is
asymmetric. A missed task stays in the meeting note. A duplicated one reaches
TASKS.md, then Google Tasks, then a person, twice. So the guard runs after the
model and is tested from the duplicate-slipping-through side.

Turkish is the reason this is not a string comparison: the same task comes back
with different suffixes every week.
"""
from pathlib import Path
import importlib.util
import os
import unittest

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("BRAINLESS_VAULT", str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "spiky_actions", ROOT / ".agents" / "scripts" / "spiky_actions.py")
spiky = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(spiky)


class TestDuplicateDetection(unittest.TestCase):
    def test_same_task_with_different_turkish_suffixes(self):
        self.assertTrue(spiky.is_duplicate(
            "Ilgaz ile ofis organizasyonunu konus",
            ["Ilgaz ile ofis organizasyonu konusulacak"]))

    def test_same_task_with_different_case_and_articles(self):
        self.assertTrue(spiky.is_duplicate(
            "Send the board pack to the investors",
            ["send board pack to investors"]))

    def test_apostrophe_and_suffix_do_not_hide_a_duplicate(self):
        self.assertTrue(spiky.is_duplicate(
            "Yedekleme script'ini test et",
            ["Yedekleme scriptini test etmek"]))

    def test_two_different_tasks_about_the_same_project_are_kept(self):
        # The expensive false positive: both mention Kuzeyfirin, both name a
        # person, and dropping one loses real work.
        self.assertFalse(spiky.is_duplicate(
            "Kuzeyfirin API dokumanini Defne'ye ilet",
            ["Kuzeyfirin katalog'unu Nehir'e goster"]))

    def test_unrelated_task_is_kept(self):
        self.assertFalse(spiky.is_duplicate(
            "Nehir'e katalog gonder", ["Defne ile butce konusunu netlestir"]))

    def test_empty_ledger_keeps_everything(self):
        self.assertFalse(spiky.is_duplicate("Lodosbank teklifini hazirla", []))

    def test_a_task_of_only_stop_words_is_dropped(self):
        self.assertTrue(spiky.is_duplicate("ve bir ile", ["anything at all"]))

    def test_empty_task_is_dropped(self):
        self.assertTrue(spiky.is_duplicate("", ["anything at all"]))


if __name__ == "__main__":
    unittest.main()
