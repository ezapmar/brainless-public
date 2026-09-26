"""The loopback note must keep a frontmatter that Obsidian can read.

file_query.py is the step that makes the wiki compound, so a note it writes has
to be findable by every reader: the regex readers in tools/ and Obsidian, which
parses frontmatter as strict YAML. Titles and summaries routinely carry a
colon ("Dialectic evening round 2026-09-25: 1 topic..."), and a bare colon-space
makes the whole block invalid YAML, so the properties vanish in Obsidian.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import concepts as C  # noqa: E402

try:
    import yaml
except ImportError:  # the public core is stdlib only
    yaml = None

TRICKY = [
    "Evening round 2026-09-25: 1 topic argued",
    "Decide: rent vs buy",
    "- starts like a list",
    "#hashtag first",
    "ends with a colon:",
    'has "quotes" inside',
    "Şirketin renkleri ne olsun",
    "plain title",
]


class YamlScalarTest(unittest.TestCase):
    def test_round_trips_through_fm_value(self):
        for v in TRICKY:
            fm = f"title: {C.yaml_scalar(v)}\n"
            self.assertEqual(C.fm_value(fm, "title"), v, fm)

    def test_plain_values_stay_plain(self):
        self.assertEqual(C.yaml_scalar("plain title"), "plain title")
        self.assertEqual(C.yaml_scalar("Şirketin renkleri ne olsun"), "Şirketin renkleri ne olsun")

    def test_newlines_collapse(self):
        self.assertEqual(C.yaml_scalar("two\nlines"), "two lines")

    def test_inner_spaces_survive(self):
        path = "Inbox/Meetings/2026-09-14 Weekly  Sync.md"
        self.assertEqual(C.yaml_scalar(path), path)

    def test_fm_value_unquotes_single_quotes(self):
        self.assertEqual(C.fm_value("title: 'it''s: fine'\n", "title"), "it's: fine")

    @unittest.skipUnless(yaml, "PyYAML not installed")
    def test_strict_yaml_accepts_every_value(self):
        for v in TRICKY:
            self.assertEqual(yaml.safe_load(f"title: {C.yaml_scalar(v)}\n")["title"], v)


MODEL_PAGE = """---
lang: tr
summary_en: Evening round 2026-09-25: 1 topic argued
title: "already quoted: fine"
tags: [query, dialectic]
compiled_at: 2026-09-25T21:37:54
notes: >
  folded block
---
# Body

key: value lines in the body stay as they are
"""


class QuoteFrontmatterTest(unittest.TestCase):
    """Model-written pages go through quote_frontmatter before they land."""

    def test_quotes_only_what_breaks(self):
        out = C.quote_frontmatter(MODEL_PAGE)
        self.assertIn('summary_en: "Evening round 2026-09-25: 1 topic argued"', out)
        self.assertIn('title: "already quoted: fine"', out)
        self.assertIn("tags: [query, dialectic]", out)
        self.assertIn("compiled_at: 2026-09-25T21:37:54", out)
        self.assertIn("notes: >\n  folded block", out)
        self.assertIn("key: value lines in the body", out)
        fm, _ = C.split_frontmatter(out)
        self.assertEqual(C.fm_value(fm, "summary_en"), "Evening round 2026-09-25: 1 topic argued")
        if yaml:
            self.assertEqual(yaml.safe_load(fm)["notes"].strip(), "folded block")

    def test_second_pass_changes_nothing(self):
        once = C.quote_frontmatter(MODEL_PAGE)
        self.assertEqual(C.quote_frontmatter(once), once)

    def test_page_without_frontmatter_untouched(self):
        self.assertEqual(C.quote_frontmatter("# Just a body: here\n"), "# Just a body: here\n")

    def test_compiler_writes_quoted_frontmatter(self):
        import compile_resources as compiler
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d)
            src = vault / "Work" / "note.md"
            src.parent.mkdir(parents=True)
            src.write_text("source")
            dst = vault / ".wiki" / "summaries" / "note.md"
            with patch.object(compiler, "VAULT", vault):
                self.assertTrue(compiler.write_compiled(dst, MODEL_PAGE, [src]))
            fm, _ = C.split_frontmatter(dst.read_text())
            self.assertIn('summary_en: "Evening round', fm)
            self.assertRegex(fm, r"sources_hash: [0-9a-f]{12}")
            if yaml:
                yaml.safe_load(fm)


class FileQueryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_fq(self, *args, stdin="# Body\n\nsome text\n"):
        env = dict(os.environ, BRAINLESS_VAULT=str(self.vault))
        return subprocess.run(
            [sys.executable, "-B", str(ROOT / "tools" / "file_query.py"), *args],
            input=stdin, capture_output=True, text=True, env=env)

    def test_colon_title_and_summary_stay_readable(self):
        title = "Decide: rent vs buy"
        summary = "Evening round 2026-09-25: 1 topic, 12/12 replies"
        r = self.run_fq("decide", title, "--summary", summary, "--date", "2026-09-26")
        self.assertEqual(r.returncode, 0, r.stderr)
        rel = r.stdout.strip()
        self.assertEqual(rel, ".wiki/digests/queries/2026-09-26-decide-decide-rent-vs-buy.md")
        fm, body = C.split_frontmatter((self.vault / rel).read_text())
        self.assertEqual(C.fm_value(fm, "title"), title)
        self.assertEqual(C.fm_value(fm, "summary_en"), summary)
        self.assertEqual(C.fm_value(fm, "command"), "decide")
        self.assertIn("some text", body)
        if yaml:
            data = yaml.safe_load(fm)
            self.assertEqual(data["summary_en"], summary)
            self.assertEqual(data["tags"], ["query", "decide"])

    def test_empty_stdin_fails_without_writing(self):
        r = self.run_fq("decide", "Nothing", stdin="   \n")
        self.assertEqual(r.returncode, 1)
        self.assertFalse((self.vault / ".wiki").exists())


if __name__ == "__main__":
    unittest.main()
