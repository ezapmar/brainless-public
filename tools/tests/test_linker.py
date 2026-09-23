"""The deterministic linker and the project-mirror rename, on a throwaway vault.

The linker writes into every summary, so the tests sit on what it must not
do: link someone else who shares a first name, link a page to itself, add a
link twice, or touch anything in a dry run.

Run: python3 -m unittest tools.tests.test_linker -v
"""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import compile_resources as compiler  # noqa: E402


def fm(**kw):
    return "---\n" + "\n".join(f"{k}: {v}" for k, v in kw.items()) + "\n---\n"


class Linker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        wiki = self.root / ".wiki"
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        for name, value in (("VAULT", self.root), ("WIKI", wiki),
                            ("ENTITY_REGISTRY", self.root / "_Agent-Context/entities.md"),
                            ("CONCEPT_REGISTRY", self.root / "_Agent-Context/concepts.md"),
                            ("CONCEPTS_DIR", wiki / "concepts")):
            stack.enter_context(patch.object(compiler, name, value))
        self.write("_Agent-Context/entities.md",
                   "Deniz Korkmaz | person | Deniz, Deniz Korkmaz | internal\n"
                   "Nordhaven | company | | partnership\n")
        self.write("_Agent-Context/concepts.md", "nakit | Nakit disiplini | cash discipline | active | |\n")
        for page in ("entities/Deniz Korkmaz", "entities/Nordhaven", "concepts/nakit"):
            self.write(f".wiki/{page}.md", fm(lang="tr", summary_en="x") + "# x\n")

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def summary(self, stem, body):
        return self.write(f".wiki/summaries/{stem}.md",
                          fm(lang="tr", summary_en="x") + f"# {stem}\n\n{body}\n\n## Bağlantılar\n")

    def test_mentions_become_links_once(self):
        p = self.summary("s1", "Deniz'le konuştuk; Nordhaven teklifi ve cash discipline konuşuldu.")
        compiler.phase_link(False, False)
        text = p.read_text()
        for target in ("Deniz Korkmaz", "Nordhaven", "nakit"):
            self.assertEqual(text.count(f"- [[{target}]]"), 1, target)
        compiler.phase_link(False, False)
        self.assertEqual(p.read_text(), text)

    def test_another_person_with_the_same_first_name_is_not_linked(self):
        p = self.summary("s2", "Deniz Kaya muhasebe tarafını anlattı.")
        compiler.phase_link(False, False)
        self.assertNotIn("[[Deniz Korkmaz]]", p.read_text())

    def test_the_full_name_and_word_boundaries_are_respected(self):
        p = self.summary("s3", "Deniz Korkmaz onayladı. Nordhavenın değil, Nordhaven'ın teklifi.")
        q = self.summary("s4", "Nordhavenlar hakkında hiçbir şey yok.")
        compiler.phase_link(False, False)
        self.assertIn("[[Deniz Korkmaz]]", p.read_text())
        self.assertIn("[[Nordhaven]]", p.read_text())
        self.assertNotIn("[[Nordhaven]]", q.read_text())

    def test_dry_run_touches_nothing(self):
        p = self.summary("s5", "Nordhaven teklifi")
        before = p.read_text()
        compiler.phase_link(True, False)
        self.assertEqual(p.read_text(), before)

    def test_a_mirror_sharing_an_entity_name_is_renamed_and_links_to_it(self):
        old = self.write(".wiki/projects/work/Nordhaven.md", fm(lang="tr", summary_en="x") + "# Nordhaven\n")
        with patch.object(compiler.subprocess, "run", return_value=type("R", (), {"returncode": 1})()):
            dst, entity = compiler._project_dst("work", "Nordhaven")
        self.assertEqual((dst.name, entity), ("Nordhaven (project).md", "Nordhaven"))
        self.assertTrue(dst.exists())
        self.assertFalse(old.exists())
        self.assertEqual(compiler._project_dst("work", "Other")[1], None)

    def test_dry_run_never_renames_a_mirror(self):
        old = self.write(".wiki/projects/work/Nordhaven.md", "x")
        compiler._project_dst("work", "Nordhaven", dry=True)
        self.assertTrue(old.exists())


if __name__ == "__main__":
    unittest.main()
