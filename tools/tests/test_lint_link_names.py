"""lint_wiki.py --fix-links follows a page's title, alias or accent variant.

What must hold: a name one page answers to resolves to that page and the
sentence keeps its wording; a name two pages claim resolves to neither; a
link whose target matches the file name stays a bare link.

Run: python3 -B -m unittest tools.tests.test_lint_link_names -v
"""
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

import lint_wiki as lw


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

    def test_entity_dossier_wins_over_the_summary_of_its_own_card(self):
        ent = self.dir / "entities"
        summ = self.dir / "summaries"
        ent.mkdir()
        summ.mkdir()
        (ent / "Ilkim Sevinc.md").write_text('---\naliases: ["İlkim Sevinç"]\n---\n# Ilkim Sevinc\n',
                                            encoding="utf-8")
        (summ / "work_about-people_ilkim-sevinc.md").write_text("# İlkim Sevinç\n", encoding="utf-8")
        names = name_index_of = lw.name_index([ent / "Ilkim Sevinc.md",
                                               summ / "work_about-people_ilkim-sevinc.md"])
        self.assertEqual(name_index_of[lw.fold_name("İlkim Sevinç")].parent.name, "entities")
        self.assertEqual(names[lw.fold_name("Ilkim Sevinc")].parent.name, "entities")

    def test_two_entities_sharing_a_name_still_resolve_to_neither(self):
        ent = self.dir / "entities"
        ent.mkdir()
        a = ent / "Selin Acar.md"
        b = ent / "Selin Uysal.md"
        a.write_text('---\naliases: ["Selin"]\n---\n# Selin Acar\n', encoding="utf-8")
        b.write_text('---\naliases: ["Selin"]\n---\n# Selin Uysal\n', encoding="utf-8")
        self.assertNotIn(lw.fold_name("Selin"), lw.name_index([a, b]))

    def test_prefix_reaches_a_longer_page_name_only_at_a_word_boundary(self):
        can = self.page("Can Çelikkol.md", "# Can Çelikkol\n")
        canan = self.page("Canan Holding.md", "# Canan Holding\n")
        cut = self.page("library_kitap-harvard-business-review-press_20260908-kitap-harvard-busine.md",
                        "# Playing to Win\n")
        kebab = self.page("bahce-tabanli-sulama-kanali.md", "# Bahce tabanli sulama kanali\n")
        other = self.page("library_makale-mckinsey_19731201-makale-mckinsey.md", "# McKinsey\n")
        pre = lw.prefix_index([can, canan, cut, kebab, other])
        self.assertEqual(pre[lw.fold_name("Can")], can)
        # The cut summary stem: the citation stops where the slug was truncated.
        self.assertEqual(pre[lw.fold_name("library_kitap-harvard-business-review-press")], cut)
        # A hyphen is not a boundary, or the first word of every kebab slug
        # would claim its page ("Bahce" is not "bahce-tabanli-sulama-kanali").
        self.assertNotIn(lw.fold_name("Bahce"), pre)
        # A path part two summaries share claims neither.
        self.assertNotIn(lw.fold_name("library"), pre)
        # Only whole words: "Cana" stops inside a name.
        self.assertIsNone(pre.get(lw.fold_name("Cana")))

    def test_prefix_two_pages_share_resolves_to_neither(self):
        a = self.page("Can Çelikkol.md", "# Can Çelikkol\n")
        b = self.page("Can Demir.md", "# Can Demir\n")
        self.assertNotIn(lw.fold_name("Can"), lw.prefix_index([a, b]))

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
