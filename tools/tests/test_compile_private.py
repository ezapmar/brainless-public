"""The compiler's guards, pinned where a mistake leaks or corrupts.

_is_private keeps sensitive homes out of the tracked .wiki layer (the 2026-08-28
audit found 88 files on GitHub because macOS hands back NFD names);
clean_alias keeps personal data out of frontmatter aliases; clean_markdown_output
is the unwrap that repaired 155 double-frontmatter summaries; known_ideas stops
one idea from getting a fresh page every night; iter_sources must not yield a
linked folder twice. No model runs here.

Run: python3 -B -m unittest discover -s tools/tests -v
"""
import atexit
import os
import shutil
import tempfile
import unicodedata
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]

# compile_resources reads the vault root at import; point it at a throwaway one
# for the import only, and leave the environment as it was for other modules.
_IMPORT_VAULT = tempfile.mkdtemp(prefix="brainless-compile-private-")
atexit.register(shutil.rmtree, _IMPORT_VAULT, True)
_OLD_VAULT = os.environ.get("BRAINLESS_VAULT")
os.environ["BRAINLESS_VAULT"] = _IMPORT_VAULT
try:
    import compile_resources as compiler
finally:
    if _OLD_VAULT is None:
        os.environ.pop("BRAINLESS_VAULT", None)
    else:
        os.environ["BRAINLESS_VAULT"] = _OLD_VAULT

EM, EN = chr(0x2014), chr(0x2013)
FENCE = "`" * 3


def nfd(s):
    return unicodedata.normalize("NFD", s)


def nfc(s):
    return unicodedata.normalize("NFC", s)


class PrivacyFixture(unittest.TestCase):
    """Fixed privacy lists, so the tests do not depend on the owner's PROFILE.md."""

    def setUp(self):
        for name, value in (
                ("PRIVATE_SEGMENTS", {nfc("Official Docs"), nfc("Özel Klasör")}),
                ("PRIVATE_SUFFIXES", (" - Health",)),
                ("PRIVATE_NAME_PARTS", (nfc("passport"), nfc("nüfus"))),
                ("PRIVATE_PATH_PARTS", (nfc("acme/finance/resources"),))):
            patcher = patch.object(compiler, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)


class IsPrivate(PrivacyFixture):
    def test_a_private_segment_anywhere_in_the_path(self):
        self.assertTrue(compiler._is_private(Path("Personal/Official Docs/id.md")))
        self.assertTrue(compiler._is_private(Path("Library/Özel Klasör/sub/deep/x.md")),
                        "a private segment in the middle still counts")

    def test_nfd_names_from_macos_are_caught(self):
        path = Path(nfd("Library/Özel Klasör/sub/x.md"))
        self.assertNotEqual(str(path), nfc(str(path)), "fixture must really be NFD")
        self.assertTrue(compiler._is_private(path))
        self.assertTrue(compiler._is_private(Path(nfd("Personal/Docs/Nüfus cüzdanı.md"))))

    def test_health_suffix_name_parts_and_sub_paths(self):
        self.assertTrue(compiler._is_private(Path("Personal/Pets/Bella - Health/vet.md")))
        self.assertTrue(compiler._is_private(Path("Personal/Travel/PASSPORT scan.md")),
                        "name parts match case-insensitively")
        self.assertTrue(compiler._is_private(Path("Work/Acme/Finance/Resources/pack.md")))

    def test_near_misses_stay_public(self):
        for p in ("Work/Acme/Finance/pack.md", "Library/Official Docs Guide.md",
                  "Personal/Recipes - Healthy/soup.md", "Library/Ozel Klasor/x.md",
                  "Library/Books/a.md"):
            self.assertFalse(compiler._is_private(Path(p)), p)

    def test_segments_match_whole_names_only(self):
        # A segment is compared whole and case-sensitively; only the name parts
        # are substring and case-insensitive.
        self.assertFalse(compiler._is_private(Path("Personal/official docs/id.md")))


class CleanAlias(unittest.TestCase):
    def test_names_and_standards_pass(self):
        for a in ("Profit First", "ISO 27001", "ISO 27001:2013", "BS 7858", "SOC 2", "Q3 2026 plan"):
            self.assertEqual(compiler.clean_alias(a), a)

    def test_personal_data_is_refused(self):
        for a in ("16.03.2021", "born 2021-03-16", "12345", "+90 532 123 45 67",
                  "£500", "$ 40", "5 TL", "500tl", "20 GBP", "ISO 27001 16/03/2021"):
            self.assertIsNone(compiler.clean_alias(a), a)

    def test_shape_rules(self):
        self.assertEqual(compiler.clean_alias(f"Acme {EM} Group"), "Acme, Group")
        self.assertEqual(compiler.clean_alias(f"3{EN}5 people"), "3-5 people")
        self.assertEqual(compiler.clean_alias("a|b"), "a/b", "a pipe would split the alias row")
        self.assertEqual(compiler.clean_alias("  padded  "), "padded")
        self.assertEqual(compiler.clean_alias(2021), "2021")
        self.assertEqual(compiler.clean_alias("x" * 60), "x" * 60)
        for a in ("x" * 61, "", "   "):
            self.assertIsNone(compiler.clean_alias(a), repr(a))

    def test_a_spelled_out_date_is_refused(self):
        self.assertIsNone(compiler.clean_alias("16 March 2021"))
        self.assertIsNone(compiler.clean_alias("16 Mart 2021"))
        self.assertIsNone(compiler.clean_alias("March 16, 2021"))
        self.assertEqual(compiler.clean_alias("Mart ayı planı"), "Mart ayı planı")

    def test_a_number_after_a_lower_case_standards_prefix_is_still_data(self):
        # Built at run time: an eleven-digit literal would trip the export's leak scan.
        self.assertIsNone(compiler.clean_alias("en 0532" + "1234567"))
        self.assertEqual(compiler.clean_alias("ISO 27001"), "ISO 27001")


class CleanMarkdownOutput(unittest.TestCase):
    clean = staticmethod(compiler.clean_markdown_output)
    DOC = "---\nlang: en\n---\n# Title\n\nBody."

    def test_unfenced_text_is_only_stripped(self):
        self.assertEqual(self.clean(f"  {self.DOC}\n\n"), self.DOC)

    def test_a_whole_document_fence_is_unwrapped(self):
        for opener in ("markdown", "md", "", "markdown  "):
            self.assertEqual(self.clean(f"{FENCE}{opener}\n{self.DOC}\n{FENCE}\n"), self.DOC, repr(opener))

    def test_chatter_around_a_fenced_frontmatter_document_is_dropped(self):
        out = f"Here is the summary:\n\n{FENCE}markdown\n{self.DOC}\n{FENCE}\n\nIf you want it saved, say so."
        self.assertEqual(self.clean(out), self.DOC)

    def test_chatter_before_a_fence_without_frontmatter_is_kept_whole(self):
        out = f"Here is the summary:\n\n{FENCE}markdown\n# Title\n{FENCE}"
        self.assertEqual(self.clean(out), out)

    def test_inner_code_blocks_survive_the_unwrap(self):
        doc = f"{self.DOC}\n\n{FENCE}python\nx = 1\n{FENCE}\n\nEnd."
        self.assertEqual(self.clean(f"{FENCE}markdown\n{doc}\n{FENCE}"), doc)

    def test_ambiguous_shapes_are_returned_untouched(self):
        unclosed = f"{FENCE}markdown\n{self.DOC}"
        fence_in_trailer = f"{FENCE}markdown\n# T\n{FENCE}\nmore {FENCE}x"
        odd_body = f"{FENCE}markdown\n# T\n{FENCE}python\nx\n{FENCE}\n{FENCE}python\n{FENCE}"
        for out in (unclosed, fence_in_trailer, odd_body):
            self.assertEqual(self.clean(out), out)

    def test_a_document_that_ends_in_a_code_block_is_not_unwrapped(self):
        doc = f"{self.DOC}\n\n{FENCE}python\nx\n{FENCE}"
        self.assertEqual(self.clean(doc), doc)

    def test_text_after_a_leading_bare_fence_counts_as_chatter(self):
        # Current behaviour: an opening bare fence is read as the wrapper, so the
        # prose after its close is dropped like trailing chatter.
        self.assertEqual(self.clean(f"{FENCE}\ncode\n{FENCE}\n\nSome text"), "code")


class KnownIdeas(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wiki = Path(self.tmp.name) / ".wiki"
        patcher = patch.object(compiler, "WIKI", self.wiki)
        patcher.start()
        self.addCleanup(patcher.stop)

    def page(self, rel, text):
        p = self.wiki / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def test_empty_wiki(self):
        self.assertEqual(compiler.known_ideas(), ({}, set()))

    def test_slug_title_and_aliases_fold_to_the_live_page(self):
        self.page("ideas/cash-first.md",
                  '---\naliases: ["Nakit önce", "Profit First"]\n---\n# Cash First Discipline\n\nText.\n')
        self.page("ideas/zz-later.md", "---\naliases: Cash first\n---\n# Another\n")
        names, archived = compiler.known_ideas()
        self.assertEqual(archived, set())
        for name in ("cash first", "cash first discipline", "nakit once", "profit first"):
            self.assertEqual(names[name], "cash-first", name)
        self.assertEqual(names["zz later"], "zz-later")
        self.assertEqual(names["another"], "zz-later")

    def test_archived_pages_are_listed_and_their_titles_reserved(self):
        self.page("ideas/live.md", "# Shared Title\n")
        self.page("_archive/ideas/old-idea.md", "# Old Idea Title\n")
        self.page("_archive/ideas/merged.md", "# Shared Title\n")
        names, archived = compiler.known_ideas()
        self.assertEqual(archived, {"old-idea", "merged"})
        self.assertEqual(names["old idea title"], "old-idea")
        self.assertEqual(names["shared title"], "live", "a live page wins a name over an archived one")
        self.assertNotIn("old idea", names, "an archived page reserves its title, not its slug")


class IterSources(PrivacyFixture):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, rel):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# x\n")
        return p

    def test_a_linked_folder_is_yielded_once(self):
        self.write("outside/notes/a.md")
        self.write("vault/Library/b.md")
        link = self.root / "vault/Library/Linked"
        try:
            link.symlink_to(self.root / "outside/notes", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not available")
        roots = [self.root / "vault/Library", link]
        got = [p.relative_to(self.root).as_posix() for p in compiler.iter_sources(roots)]
        self.assertEqual(sorted(got), ["vault/Library/Linked/a.md", "vault/Library/b.md"])
        self.assertEqual(len(got), len(set(got)))

    def test_overlapping_roots_missing_roots_and_private_files(self):
        self.write("vault/Library/Sub/c.md")
        self.write("vault/Library/top.md")
        self.write("vault/Library/Official Docs/id.md")
        self.write("vault/Library/Sub/notes.txt")
        roots = [self.root / "vault/Library", self.root / "vault/Library/Sub", self.root / "vault/Missing"]
        got = [p.relative_to(self.root).as_posix() for p in compiler.iter_sources(roots)]
        self.assertEqual(sorted(got), ["vault/Library/Sub/c.md", "vault/Library/top.md"])


if __name__ == "__main__":
    unittest.main()
