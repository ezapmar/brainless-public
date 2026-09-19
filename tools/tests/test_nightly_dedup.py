"""The nightly digest's task sync, tested on a throwaway ledger.

The ledger reached 130 open rows because the digest matched titles character
for character: a promise the model rephrased the next night became a new row.
The guard is the one spiky_actions.py already had, moved to tools/task_dedup.py
so both writers share it. These tests pin the two behaviours that matter: a
rephrased task is dropped, a genuinely new one is not.
"""
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))


class NightlyDedupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        os.environ["BRAINLESS_VAULT"] = str(self.vault)
        (self.vault / "_Agent-Context").mkdir(parents=True)
        (self.vault / "Thinking" / "Daily").mkdir(parents=True)
        import nightly_processor
        self.np = importlib.reload(nightly_processor)
        self.ledger = self.vault / "_Agent-Context" / "TASKS.md"
        self.ledger.write_text(
            "# Ledger\n\n## Promises\n\n"
            "- [ ] Kuzeybank entegrasyonu için Defne ile vergi konusunu görüş | [[2026-09-10]] | 2026-09-10\n"
            "- [x] Aylin'e dashboard örneklerini gönder | [[2026-09-11]] | 2026-09-11\n")

    def tearDown(self):
        self.tmp.cleanup()

    def sync(self, *titles):
        digest = "# D\n\n## Action Items\n" + "".join(f"- [ ] {t}\n" for t in titles)
        self.np.sync_tasks(digest, "2026-09-19")
        return self.ledger.read_text()

    def test_a_rephrased_task_is_not_added_again(self):
        text = self.sync("Defne ile Kuzeybank entegrasyonunun vergi konusunu görüşmek")
        self.assertEqual(text.count("- [ ]"), 1)

    def test_a_task_already_closed_is_not_reopened_in_other_words(self):
        text = self.sync("Dashboard örneklerini Aylin'e gönder")
        self.assertEqual(text.count("Aylin"), 1)

    def test_a_new_task_is_added_once(self):
        text = self.sync("Lodosbank teklif toplantısı için sunumu hazırla",
                         "Lodosbank teklif toplantısının sunumunu hazırla")
        self.assertEqual(text.count("Lodosbank"), 1)
        self.assertIn("| [[2026-09-19]] | 2026-09-19", text)

    def test_the_budget_line_counts_labels(self):
        notes = ["/x/a.md", "/x/b.md", "/x/c.md"]
        line = self.np.budget_line(notes, {"a.md": "task", "b.md": "epic"})
        self.assertIn("1", line)
        self.assertNotIn("\u2014", line)


if __name__ == "__main__":
    unittest.main()
