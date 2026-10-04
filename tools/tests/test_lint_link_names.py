"""lint_wiki.py --fix-links follows a page's title, alias or accent variant.

What must hold: a name one page answers to resolves to that page and the
sentence keeps its wording; a name two pages claim resolves to neither; a
link whose target matches the file name stays a bare link.

Run: python3 -B -m unittest tools.tests.test_lint_link_names -v
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import lint_wiki as lw  # noqa: E402


class LinkNames(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def page(self, name, text):
        p = self.dir / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_fold_name_ignores_case_accents_and_separators(self):
        self.assertEqual(lw.fold_name("Köprü Çayı"), lw.fold_name("kopru-cayi"))
        self.assertEqual(lw.fold_name("Kalıcı  İş"), "kalici is")

    def test_index_holds_names_one_page_answers_to(self):
        a = self.page("Acme Robotics.md", '---\naliases: ["Acme", "Shared"]\n---\n# Acme Robotiks\n')
        b = self.page("other.md", '---\naliases: ["Shared"]\n---\n# Another Page\n')
        names = lw.name_index([a, b])
        self.assertEqual(names[lw.fold_name("Acme")], a)
        self.assertEqual(names[lw.fold_name("acme robotiks")], a)
        self.assertEqual(names[lw.fold_name("Another Page")], b)
        self.assertNotIn(lw.fold_name("Shared"), names)

    def test_remapped_link_keeps_its_wording(self):
        target = self.page("Acme Robotics.md", "# Acme Robotics\n")
        src = self.page("note.md", "Ask [[Acme]] and [[acme robotics]] and [[Acme|them]]. [[Nobody]] stays.\n")
        hits = {"Acme": target, "acme robotics": target}
        counts = lw.fix_links_in_file(src, {"Acme", "acme robotics", "Nobody"}, hits.get)
        self.assertEqual(counts, {"remapped": 3, "delinked": 0})
        self.assertEqual(src.read_text(encoding="utf-8"),
                         "Ask [[Acme Robotics|Acme]] and [[Acme Robotics]] and [[Acme Robotics|them]]. [[Nobody]] stays.\n")


if __name__ == "__main__":
    unittest.main()
