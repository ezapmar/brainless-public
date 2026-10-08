"""One frontmatter parser for every tool (tools/frontmatter.py)."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
import frontmatter as fm

PAGE = '---\nlang: tr\nsummary_en: "A \\"quoted\\" gist"\nowner: \'single\'\ntags:\n  - a\nnote: value # a comment\n---\n\n# Title\nbody\n'


class SplitTests(unittest.TestCase):
    def test_split_and_strip(self):
        header, body = fm.split(PAGE)
        self.assertTrue(header.startswith("lang: tr"))
        self.assertEqual(body, "\n# Title\nbody\n")
        self.assertEqual(fm.strip(PAGE), body)

    def test_no_block(self):
        self.assertEqual(fm.split("# Title\n"), ("", "# Title\n"))
        self.assertFalse(fm.has("# Title\n"))
        self.assertFalse(fm.has("---\nunclosed"))
        self.assertEqual(fm.strip(""), "")

    def test_tolerates_trailing_blanks_crlf_and_eof_fence(self):
        self.assertTrue(fm.has("--- \nlang: en\n---  \nbody"))
        self.assertTrue(fm.has("---\r\nlang: en\r\n---\r\nbody"))
        self.assertTrue(fm.has("---\nlang: en\n---"))
        self.assertEqual(fm.parse("---\nlang: en\n---")["lang"], "en")

    def test_horizontal_rule_in_body_is_not_a_block(self):
        self.assertFalse(fm.has("Intro\n---\nlang: en\n---\n"))


class ParseTests(unittest.TestCase):
    def test_values(self):
        d = fm.parse(PAGE)
        self.assertEqual(d["lang"], "tr")
        self.assertEqual(d["summary_en"], 'A "quoted" gist')
        self.assertEqual(d["owner"], "single")
        self.assertEqual(d["tags"], "")
        self.assertEqual(d["note"], "value # a comment")
        self.assertNotIn("- a", d)

    def test_comments_option(self):
        self.assertEqual(fm.parse(PAGE, comments=True)["note"], "value")
        self.assertEqual(fm.parse("---\nurl: https://x.y/#frag\n---\n", comments=True)["url"], "https://x.y/#frag")

    def test_colon_inside_value(self):
        self.assertEqual(fm.parse("---\ntime: 11:30\n---\n")["time"], "11:30")


class SetKeyTests(unittest.TestCase):
    def test_replace_add_and_create(self):
        self.assertEqual(fm.set_key(PAGE, "lang", "en").split("\n")[1], "lang: en")
        out = fm.set_key(PAGE, "status", "decided")
        self.assertIn("note: value # a comment\nstatus: decided\n---\n", out)
        self.assertTrue(out.endswith("\n# Title\nbody\n"))
        self.assertEqual(fm.set_key("body\n", "k", "v"), "---\nk: v\n---\nbody\n")

    def test_render(self):
        self.assertEqual(fm.render({"lang": "tr", "n": 1}), "---\nlang: tr\nn: 1\n---\n")


if __name__ == "__main__":
    unittest.main()
