"""The compiler's guards, pinned where a mistake leaks or corrupts.

_is_private keeps sensitive homes out of the tracked .wiki layer (the 2026-08-28
audit found 88 files on GitHub because macOS hands back NFD names);
clean_alias keeps personal data out of frontmatter aliases; clean_markdown_output
is the unwrap that repaired 155 double-frontmatter summaries; known_ideas stops
one idea from getting a fresh page every night; iter_sources must not yield a
linked folder twice. No model runs here.

P0.4 adds the cloud gate: a source under a cloud_exclude folder, or one marked
privacy: personal or secret, must not reach a cloud provider. The tests below are
written from the leaking side, because the failure is silent: a prompt that
carries the note anyway still returns a good answer.

Run: python3 tools/run_tests.py test_compile_private
     python3 tools/run_tests.py            (the whole suite, no PYTHONPATH)
"""
import atexit
import os
import shutil
import tempfile
import unicodedata
import unittest
from datetime import datetime, timedelta
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
    import cloud_gate
    import llm
finally:
    if _OLD_VAULT is None:
        os.environ.pop("BRAINLESS_VAULT", None)
    else:
        os.environ["BRAINLESS_VAULT"] = _OLD_VAULT

from test_llm_lanes import Env  # noqa: E402  (needs tools/tests on sys.path)

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


class Rooted(unittest.TestCase):
    """A throwaway vault root, so cloud_gate's path matching is exercised for real.

    cloud_gate resolves a path against its own VAULT constant, which was captured
    at import. Patching it here keeps the tests off the owner's actual tree while
    still going through rel_of / matching_root rather than a stub.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name, value in (("VAULT", self.root), ("CLOUD_EXCLUDE", ()), ("_cache", {})):
            patcher = patch.object(cloud_gate, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write(self, rel, text="# x\n"):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p


class CloudExcludeLane(Rooted):
    def test_a_cloud_excluded_source_picks_the_local_lane(self):
        with patch.object(cloud_gate, "CLOUD_EXCLUDE", ("Personal/Move Abroad",)):
            local = self.write("Personal/Move Abroad/school.md")
            safe = self.write("Library/Books/a.md")
            self.assertEqual(compiler._compile_lane(local), "compile-local")
            self.assertEqual(compiler._compile_lane(safe), "compile")
            self.assertTrue(compiler._requires_local(local))
            self.assertFalse(compiler._requires_local(safe))

    def test_the_folder_rule_is_not_a_hard_skip(self):
        # Decision (b): the folder is compiled, just locally. Folding it into
        # _is_private would silently produce the "no summary at all" option,
        # which is a different decision than the one that was taken.
        with patch.object(cloud_gate, "CLOUD_EXCLUDE", ("Personal/Pets",)):
            pets = self.write("Personal/Pets/vet.md")
            self.assertFalse(compiler._is_private(pets))
            got = list(compiler.iter_sources([self.root / "Personal"]))
            self.assertEqual(got, [pets])

    def test_a_nfd_folder_name_still_matches(self):
        with patch.object(cloud_gate, "CLOUD_EXCLUDE", (nfc("Personal/Şeker"),)):
            path = self.write(nfd("Personal/Şeker/note.md"))
            self.assertTrue(compiler._requires_local(path))


class CloudGateRefusal(Rooted):
    """llm.py is the second half of the guarantee: the compiler picks a lane,
    the gate refuses the call if the lane is wrong anyway."""

    def setUp(self):
        super().setUp()
        self.calls = []
        patcher = patch.object(
            llm, "_dispatch",
            side_effect=lambda provider, *a, **k: self.calls.append(provider) or None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_cloud_excluded_source_never_reaches_a_cloud_prompt(self):
        with patch.object(cloud_gate, "CLOUD_EXCLUDE", ("Personal/Move Abroad",)):
            src = self.write("Personal/Move Abroad/school.md")
            with Env(BRAINLESS_LLM_PROVIDER_COMPILE="claude-cli", BRAINLESS_LLM_LOCAL_ONLY=None):
                self.assertIsNone(llm.run_prompt("prompt", lane="compile", sources=[src]))
        self.assertEqual(self.calls, [], "the dispatcher must not be called at all")

    def test_a_safe_source_still_reaches_the_cloud(self):
        src = self.write("Library/Books/a.md")
        with Env(BRAINLESS_LLM_PROVIDER_COMPILE="claude-cli", BRAINLESS_LLM_LOCAL_ONLY=None):
            llm.run_prompt("prompt", lane="compile", sources=[src])
        self.assertEqual(self.calls, ["claude-cli"])

    def test_an_excluded_source_is_allowed_on_the_local_lane(self):
        with patch.object(cloud_gate, "CLOUD_EXCLUDE", ("Personal/Pets",)):
            src = self.write("Personal/Pets/vet.md")
            with Env(BRAINLESS_LLM_PROVIDER_COMPILE_LOCAL="openai-compatible",
                     BRAINLESS_LLM_BASE_URL="http://127.0.0.1:8080/v1",
                     BRAINLESS_LLM_LOCAL_ONLY=None):
                llm.run_prompt("prompt", lane="compile-local", sources=[src])
        self.assertEqual(self.calls, ["openai-compatible"])

    def test_a_refusal_is_written_to_the_egress_ledger_as_unsent(self):
        import json
        egress = Path(tempfile.mkdtemp(prefix="brainless-egress-")) / "egress.jsonl"
        self.addCleanup(shutil.rmtree, str(egress.parent), True)
        with patch.object(llm, "EGRESS_FILE", str(egress)), \
                patch.object(cloud_gate, "CLOUD_EXCLUDE", ("Personal/Pets",)):
            src = self.write("Personal/Pets/vet.md")
            with Env(BRAINLESS_LLM_PROVIDER_COMPILE="claude-cli", BRAINLESS_LLM_LOCAL_ONLY=None):
                llm.run_prompt("prompt", lane="compile", sources=[src])
        row = json.loads(egress.read_text(encoding="utf-8").strip())
        self.assertFalse(row["sent"])
        self.assertTrue(row["blocked"])
        self.assertEqual(row["refused"], "cloud_exclude")
        self.assertNotIn("Personal/Pets", egress.read_text(encoding="utf-8"),
                         "the ledger holds no source path")


class PrivacyFrontmatter(Rooted):
    def test_secret_is_never_compiled(self):
        secret = self.write("Work/Plan.md", "---\nprivacy: secret\n---\n# Plan\nSECRET\n")
        self.assertTrue(compiler._is_private(secret))
        self.assertEqual(list(compiler.iter_sources([self.root / "Work"])), [],
                         "a secret note must not reach the summary phase at all")

    def test_personal_is_compiled_locally_not_skipped(self):
        card = self.write("Work/People/Ada.md", "---\nprivacy: personal\n---\n# Ada\n")
        self.assertFalse(compiler._is_private(card))
        self.assertTrue(compiler._requires_local(card))
        self.assertEqual(compiler._compile_lane(card), "compile-local")

    def test_personal_is_refused_in_the_cloud(self):
        card = self.write("Work/People/Ada.md", "---\nprivacy: personal\n---\n# Ada\n")
        calls = []
        with patch.object(llm, "_dispatch",
                          side_effect=lambda provider, *a, **k: calls.append(provider) or None), \
                Env(BRAINLESS_LLM_PROVIDER_COMPILE="claude-cli", BRAINLESS_LLM_LOCAL_ONLY=None):
            self.assertIsNone(llm.run_prompt("prompt", lane="compile", sources=[card]))
        self.assertEqual(calls, [])

    def test_internal_and_public_are_not_blocked(self):
        for level in ("internal", "public"):
            note = self.write(f"Work/{level}.md", f"---\nprivacy: {level}\n---\n# {level}\n")
            self.assertIsNone(cloud_gate.block_reason(note), level)
            self.assertEqual(compiler._compile_lane(note), "compile")

    def test_a_trailing_comment_does_not_change_the_level(self):
        note = self.write("Work/n.md", "---\nprivacy: internal (no customer names)\n---\n# n\n")
        self.assertIsNone(cloud_gate.block_reason(note))
        self.assertEqual(cloud_gate.level_of("internal (no customer names)"), "internal")

    def test_an_unreadable_value_blocks_instead_of_unlocking(self):
        # A typo in a security field must fail closed. One real note already
        # carries a sentence here, written before the field had four values.
        note = self.write("Work/t.md", "---\nprivacy: Tier A only (Finance Data rule)\n---\n# t\n")
        self.assertEqual(cloud_gate.block_reason(note), "privacy:unrecognised")
        self.assertTrue(compiler._requires_local(note))
        self.assertFalse(compiler._is_private(note), "unreadable is local, not dropped")


class LinkMeta(Rooted):
    """A cross-link must survive the exclusion, and the content must not.

    The concept page is the case that matters: it is the one page built by
    folding several sources together, so its prompt is where a private note would
    ride along with a public one.
    """

    def members(self):
        safe = self.write("Library/Books/a.md", "# A\nSAFE BODY\n")
        excluded = self.write("Personal/Move Abroad/School.md", "# School\nPRIVATE BODY\n")
        return [
            {"stem": "personal_move_abroad_school",
             "source": "Personal/Move Abroad/School.md", "path": excluded},
            {"stem": "library_books_a", "source": "Library/Books/a.md", "path": safe},
        ]

    def test_a_mixed_concept_moves_whole_to_the_local_lane(self):
        # Not just the blocked body: the page on disk and the prior article are
        # derivatives of the same sources, so the entire call stays on the Mac.
        with patch.object(cloud_gate, "CLOUD_EXCLUDE", ("Personal/Move Abroad",)):
            lane, sources = compiler._concept_inputs(self.members())
        self.assertEqual(lane, "compile-local")
        self.assertEqual(sources, ["Personal/Move Abroad/School.md", "Library/Books/a.md"],
                         "the local lane keeps every source, so no link is lost")

    def test_a_concept_with_no_blocked_member_stays_on_its_lane(self):
        only_safe = self.members()[1:]
        lane, sources = compiler._concept_inputs(only_safe)
        self.assertIsNone(lane)
        self.assertEqual(sources, ["Library/Books/a.md"])

    def test_the_overview_keeps_a_meta_line_for_withheld_pages(self):
        concept = self.write("Work/concepts/moving.md",
                             "---\ntype: concept\nsensitivity: personal\n---\n# Moving\nPRIVATE BODY\n")
        counts, _ = compiler.C.split_frontmatter(compiler.C.read_page(concept))
        self.assertTrue(compiler._concept_page_personal(concept))
        prompt = compiler._overview_prompt([], withheld=2)
        self.assertIn("2 concept page(s) are withheld", prompt)
        self.assertNotIn("PRIVATE BODY", prompt,
                         "the count is the meta, the body never leaves the Mac")
        self.assertTrue(counts, "fixture sanity: the page really has frontmatter")

    def test_an_owner_marked_page_counts_as_personal(self):
        page = self.write("Work/concepts/x.md",
                          "---\ntype: concept\nprivacy: personal\n---\n# X\n")
        self.assertTrue(compiler._concept_page_personal(page))


class CloudExcludeHealth(unittest.TestCase):
    def setUp(self):
        import health_check
        self.health = health_check
        patcher = patch.object(health_check, "CLOUD_EXCLUDE", ["Personal/Pets"])
        patcher.start()
        self.addCleanup(patcher.stop)

    def row(self, *, provider="claude-cli", blocked=True, sent=True, hours_ago=1,
            phase="start"):
        stamp = (datetime.now() - timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")
        row = {"ts": stamp, "id": "1", "phase": phase, "lane": "compile",
               "provider": provider, "sent": sent}
        if blocked:
            row["blocked"] = True
        return row

    def findings(self, ledger):
        return self.health.cloud_exclude_findings(
            exists=lambda rel: True, ledger=ledger,
            is_local=lambda provider: provider != "claude-cli")

    def test_a_renamed_folder_is_red(self):
        status, detail = self.health.cloud_exclude_findings(
            exists=lambda rel: False, ledger=[],
            is_local=lambda provider: True)
        self.assertEqual(status, "RED")
        self.assertIn("Personal/Pets", detail)

    def test_a_cloud_line_marked_blocked_is_red(self):
        self.assertEqual(self.findings([self.row()])[0], "RED")

    def test_a_local_line_marked_blocked_is_fine(self):
        self.assertEqual(self.findings([self.row(provider="openai-compatible")])[0], "OK")

    def test_a_refused_call_is_warned_not_red(self):
        status, _ = self.findings([self.row(sent=False)])
        self.assertEqual(status, "WARN")

    def test_an_old_line_is_ignored(self):
        self.assertEqual(self.findings([self.row(hours_ago=30)])[0], "OK")

    def test_a_completion_line_is_not_a_send(self):
        self.assertEqual(self.findings([self.row(phase="end")])[0], "OK")

    def test_no_list_warns(self):
        with patch.object(self.health, "CLOUD_EXCLUDE", []):
            status, _ = self.findings([])
        self.assertEqual(status, "WARN")


class StampMark(Rooted):
    """A page written from a blocked source carries `sensitivity: personal`.

    That flag is what keeps the later phases (aliases, overview) on the local
    lane too; without it a project mirror of a private folder would send its own
    gist to the cloud one phase later.
    """

    def setUp(self):
        super().setUp()
        for name in ("VAULT", "WIKI"):
            patcher = patch.object(compiler, name,
                                   self.root if name == "VAULT" else self.root / ".wiki")
            patcher.start()
            self.addCleanup(patcher.stop)
        self.dst = self.root / ".wiki" / "summaries" / "x.md"

    def test_a_blocked_source_marks_the_page(self):
        src = self.write("Personal/Pets/vet.md", "# src\n")
        with patch.object(cloud_gate, "CLOUD_EXCLUDE", ("Personal/Pets",)):
            self.assertTrue(compiler.write_compiled(self.dst, "# Vet\n\nText.\n", [src]))
        self.assertIn("sensitivity: personal", self.dst.read_text(encoding="utf-8"))

    def test_a_clean_source_does_not_steal_the_field(self):
        src = self.write("Library/Books/a.md", "# src\n")
        self.assertTrue(compiler.write_compiled(self.dst, "# A\n\nText.\n", [src]))
        self.assertNotIn("sensitivity", self.dst.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
