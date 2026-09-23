"""The concept layer, tested where it can lose something.

A concept page is rewritten whole by a model every time a source arrives, so
the tests sit on the promises that rewrite could break: a disagreement stays
recorded, a struck claim stays struck, a cited source stays cited, people and
finance never feed a concept, and a family concept never names itself on Buzz.
No model runs here; call_claude is replaced by canned answers.

Run: python3 -m unittest tools.tests.test_concepts -v
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import concepts as C  # noqa: E402
import compile_resources as compiler  # noqa: E402

DASH = "\u2014"


def fm(**kw):
    return "---\n" + "\n".join(f"{k}: {v}" for k, v in kw.items()) + "\n---\n"


PAGE = fm(lang="tr", summary_en="Cash first.", type="concept") + """# Nakit disiplini

## Mevcut Pozisyon
- Kâr önce ayrılır ([[profit-first]], 2024, primary)

## Tartışmalı
- Aylık kapanış yeterli ([[simple-numbers]]) ile haftalık kapanış şart ([[cfo-notes]]) çelişiyor.

## Geçersizleşenler
- ~~Runway 18 ay~~ superseded 2026-05: yeni bütçe ([[budget-2026]])
"""


class Registry(unittest.TestCase):
    def test_rows_need_a_known_status_and_prose_with_pipes_is_skipped(self):
        text = """# Concept registry
Row format: `slug | Title | aliases | status | sensitivity | scope`.
nakit-disiplini | Nakit disiplini | cash discipline, profit first | active | | cash before growth
okul-gecisi | Okul geçişi | | proposed | personal | school moves
Bad Slug | x | | active | |
old-idea | Old | | retired | |
"""
        rows = C.parse_registry(text)
        self.assertEqual([r["slug"] for r in rows], ["nakit-disiplini", "okul-gecisi", "old-idea"])
        self.assertEqual(rows[0]["aliases"], ["cash discipline", "profit first"])
        self.assertEqual(rows[1]["sensitivity"], "personal")

    def test_a_row_round_trips(self):
        row = {"slug": "a-b", "title": "A B", "aliases": ["x", "y"], "status": "active",
               "sensitivity": "personal", "scope": "one line"}
        self.assertEqual(C.parse_registry(C.registry_row(row))[0], row)


class Scope(unittest.TestCase):
    AREA = "Work/Acme"

    def test_knowledge_roots_are_in(self):
        for src in ("Library/Books/Management/x.md", "Library/Books/Finance/profit.md", "Thinking/Daily/d.md",
                    "Personal/Content/c.md", "Work/Acme/Partnerships/p.md"):
            self.assertTrue(C.in_scope(src, self.AREA), src)

    def test_people_crm_spiky_archive_and_finance_are_out(self):
        for src in ("Work/Acme/About People/Ozan.md", "Inbox/CRM/deal.md",
                    "Inbox/Spiky/2026-09-01 x.md", "Archive/Spiky/2025-08/x.md",
                    "Work/Acme/Finance/pack.md", "Work/Acme/Investor Relations/r.md",
                    "Library/People/Employee List - Apr 2026/x.md", "Work/Other Co/x.md", ""):
            self.assertFalse(C.in_scope(src, self.AREA), src)

    def test_health_and_family_sources_are_personal(self):
        self.assertTrue(C.is_personal("Personal/Pets/Rex/vet.md"))
        self.assertTrue(C.is_personal("Personal/Hukuk/case.md"))
        self.assertFalse(C.is_personal("Library/Books/Management/x.md"))


class Frontmatter(unittest.TestCase):
    def test_concepts_key_none_means_never_assigned_and_empty_means_none_fit(self):
        self.assertIsNone(C.read_concepts_key(fm(lang="tr") + "x"))
        self.assertEqual(C.read_concepts_key(fm(concepts="[]") + "x"), [])
        self.assertEqual(C.read_concepts_key(fm(concepts='["a", "b"]') + "x"), ["a", "b"])

    def test_set_key_replaces_or_adds_without_touching_the_body(self):
        text = fm(lang="tr", concepts="[]") + "body --- here\n"
        out = C.set_fm_key(C.set_fm_key(text, "concepts", '["a"]'), "concepts_rev", "abc")
        self.assertEqual(C.read_concepts_key(out), ["a"])
        self.assertIn("concepts_rev: abc", out)
        self.assertTrue(out.endswith("body --- here\n"))

    def test_members_round_trip(self):
        text = C.set_fm_key(fm(lang="tr") + "x", "members", C.members_value({"b": "2", "a": "1"}))
        self.assertEqual(C.read_members(text), {"a": "1", "b": "2"})


class Delta(unittest.TestCase):
    def test_new_and_changed_members_are_due_and_unchanged_ones_are_not(self):
        assigned = {"new": "h1", "changed": "h2", "same": "h3"}
        integrated = {"changed": "old", "same": "h3", "dropped": "h9"}
        self.assertEqual(C.delta(assigned, integrated), ["changed", "new"])


class Assignment(unittest.TestCase):
    def test_unknown_stems_and_slugs_are_dropped_and_skipped_stems_get_empty(self):
        out = json.dumps({"assign": {"s1": ["nakit", "invented"], "ghost": ["nakit"]},
                          "proposals": [{"title": "Okul geçişi", "members": ["s1", "s2", "ghost"]}]})
        assign, props = C.parse_assignment("chatter " + out, {"s1", "s2"}, {"nakit"})
        self.assertEqual(assign, {"s1": ["nakit"], "s2": []})
        self.assertEqual(props[0]["slug"], "okul-gecisi")
        self.assertEqual(sorted(props[0]["members"]), ["s1", "s2"])

    def test_malformed_output_assigns_nothing(self):
        self.assertEqual(C.parse_assignment("not json", {"s1"}, {"a"}), ({}, []))

    def test_proposals_are_deduped_against_aliases_and_need_two_sources(self):
        rows = C.parse_registry("nakit | Nakit | profit first | active | |")
        props = [
            {"slug": "profit-first", "title": "Profit First", "aliases": [], "scope": "", "members": ["a", "b"]},
            {"slug": "tek", "title": "Tek kaynak", "aliases": [], "scope": "", "members": ["a"]},
            {"slug": "okul", "title": "Okul", "aliases": [], "scope": "", "members": ["a", "b"]},
            {"slug": "okul", "title": "Okul", "aliases": [], "scope": "", "members": ["c", "d"]},
        ]
        self.assertEqual([p["slug"] for p in C.new_proposals(props, rows)], ["okul"])


class Validator(unittest.TestCase):
    def test_a_faithful_update_passes(self):
        new = PAGE.replace("## Tartışmalı", "- Yeni iddia ([[saas-playbook]], secondary)\n\n## Tartışmalı")
        self.assertEqual(C.validate_update(PAGE, new), [])

    def test_dropping_a_struck_claim_is_refused(self):
        new = PAGE.replace("- ~~Runway 18 ay~~ superseded 2026-05: yeni bütçe ([[budget-2026]])",
                           "- Runway 12 ay ([[budget-2026]])")
        self.assertTrue(any("superseded" in p for p in C.validate_update(PAGE, new)))

    def test_resolving_a_disagreement_by_dropping_one_side_is_refused(self):
        new = PAGE.replace(" ile haftalık kapanış şart ([[cfo-notes]])", "")
        self.assertTrue(any("cfo-notes" in p for p in C.validate_update(PAGE, new)))

    def test_a_dash_or_a_shrunken_page_is_refused(self):
        self.assertTrue(C.validate_update(PAGE, PAGE + f"a {DASH} b\n"))
        short = PAGE.split("## Mevcut")[0] + " ".join(f"[[{s}]]" for s in C.cited_links(PAGE)) + " ~~Runway 18 ay~~"
        self.assertTrue(any("shrank" in p for p in C.validate_update(PAGE, short)))

    def test_a_first_page_only_needs_its_own_shape(self):
        self.assertEqual(C.validate_update("", PAGE), [])
        self.assertTrue(C.validate_update("", "# no frontmatter\n"))

    def test_dash_repair(self):
        self.assertEqual(C.strip_dashes(f"a {DASH} b, 3\u20135"), "a, b, 3-5")


class Phases(unittest.TestCase):
    """Assign and update against a throwaway vault with canned model answers."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        wiki = self.root / ".wiki"
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
        for name, value in (("VAULT", self.root), ("WIKI", wiki), ("COMPANY_AREA", "Work/Acme"),
                            ("CONCEPT_REGISTRY", self.root / "_Agent-Context/concepts.md"),
                            ("CONCEPTS_DIR", wiki / "concepts"),
                            ("CONCEPT_ARCHIVE", wiki / "_archive/articles"),
                            ("_DEADLINE", None)):
            stack.enter_context(patch.object(compiler, name, value))
        self.pings = []
        stack.enter_context(patch.object(compiler, "_ping_proposals", side_effect=self.pings.append))
        self.answers = []
        self.prompts = []
        stack.enter_context(patch.object(compiler, "call_claude", side_effect=self.answer))
        self.write("_Agent-Context/concepts.md",
                   "# Concept registry\n\n## Concepts\nnakit | Nakit | | active | | cash\n")
        self.summary("lib-a", "Library/Books/a.md", "h1")
        self.summary("lib-b", "Library/Articles/b.md", "h2")
        self.summary("people", "Work/Acme/About People/x.md", "h3")
        self.summary("dog", "Personal/Pets/Bella/vet.md", "h4")

    def answer(self, prompt, *a, **kw):
        self.prompts.append(prompt)
        return self.answers.pop(0) if self.answers else None

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def summary(self, stem, source, h):
        return self.write(f".wiki/summaries/{stem}.md",
                          fm(lang="tr", summary_en=f"gist {stem}", source=source, sources_hash=h)
                          + f"# {stem}\n\n## Bağlantılar\n")

    def test_assign_stamps_in_scope_summaries_only_and_files_proposals(self):
        self.answers.append(json.dumps({
            "assign": {"lib-a": ["nakit"], "lib-b": [], "dog": []},
            "proposals": [{"title": "Evcil hayvan bakımı", "members": ["dog", "lib-b"]}]}))
        compiler.phase_concept_assign(False, False)
        self.assertNotIn("gist people", self.prompts[0])
        read = lambda s: C.read_concepts_key((self.root / f".wiki/summaries/{s}.md").read_text())
        self.assertEqual(read("lib-a"), ["nakit"])
        self.assertEqual(read("lib-b"), ["evcil-hayvan-bakimi"])
        self.assertIsNone(read("people"))
        rows = C.parse_registry((self.root / "_Agent-Context/concepts.md").read_text())
        self.assertEqual(rows[-1]["status"], "proposed")
        self.assertEqual(rows[-1]["sensitivity"], "personal")
        self.assertEqual(len(self.pings[0]), 1)
        # A second night asks nothing: every summary carries the current revision.
        compiler.phase_concept_assign(False, False)
        self.assertEqual(len(self.prompts), 1)

    def test_update_keeps_old_page_when_the_model_drops_history(self):
        for s in ("lib-a", "lib-b"):
            p = self.root / f".wiki/summaries/{s}.md"
            p.write_text(C.set_fm_key(p.read_text(), "concepts", '["nakit"]'))
        page = self.write(".wiki/concepts/nakit.md",
                          C.set_fm_key(PAGE, "members", C.members_value({"lib-a": "h1"})))
        bad = PAGE.replace("~~Runway 18 ay~~", "Runway 18 ay")
        self.answers += [bad, bad]
        compiler.phase_concepts(False, False)
        self.assertEqual(page.read_text(), C.set_fm_key(PAGE, "members", C.members_value({"lib-a": "h1"})))
        self.assertIn("lib-b", self.prompts[0])
        self.assertNotIn("--- [[lib-a]]", self.prompts[0])

    def test_update_writes_members_and_backlinks_when_the_page_is_faithful(self):
        p = self.root / ".wiki/summaries/lib-b.md"
        p.write_text(C.set_fm_key(p.read_text(), "concepts", '["nakit"]'))
        self.answers.append(PAGE.replace("## Tartışmalı", "- Yeni ([[lib-b]], secondary)\n\n## Tartışmalı"))
        compiler.phase_concepts(False, False)
        page = (self.root / ".wiki/concepts/nakit.md").read_text()
        self.assertEqual(C.read_members(page), {"lib-b": "h2"})
        self.assertIn("type: concept", page)
        self.assertIn("- [[nakit]]", p.read_text())
        # Nothing new the next night, so no call.
        compiler.phase_concepts(False, False)
        self.assertEqual(len(self.prompts), 1)


class Preamble(unittest.TestCase):
    def test_chatter_before_the_frontmatter_is_dropped(self):
        row = {"aliases": [], "slug": "nakit"}
        out = compiler._finish_concept("Here is the updated page:\n\n" + PAGE, row, {"a": "1"}, False)
        self.assertTrue(out.startswith("---\n"))
        self.assertEqual(C.validate_update("", out), [])


class PingPrivacy(unittest.TestCase):
    def test_personal_titles_never_reach_buzz(self):
        sent = []
        fake = type(sys)("buzz_delivery")
        fake.send = lambda channel, body, **kw: sent.append(body)
        rows = [{"slug": "okul", "title": "Okul geçişi", "sensitivity": "personal"},
                {"slug": "nakit", "title": "Nakit", "sensitivity": ""}]
        with patch.dict(sys.modules, {"buzz_delivery": fake}), \
                contextlib.redirect_stdout(io.StringIO()):
            compiler._ping_proposals(rows)
        self.assertIn("Nakit", sent[0])
        self.assertNotIn("Okul", sent[0])


if __name__ == "__main__":
    unittest.main()
