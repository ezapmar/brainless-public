"""Model chatter and nested pages are caught; real content that mentions tools is not.

The cases are the ten pages found on 2026-09-22 (shortened), plus the three
shapes that must stay legal: a page that discusses a write tool in its body, a
template in a code block, an ordinary summary.

Run: python3 -m unittest tools.tests.test_output_guard -v
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
import output_guard as G  # noqa: E402
import compile_resources as compiler  # noqa: E402
import concepts as C  # noqa: E402

FM = "---\nlang: tr\nsummary_en: {}\ncompiled_at: 2026-09-20T22:00:13\n---\n"
GOOD = FM.format("A book on decisions under uncertainty.") + "# Thinking in Bets\n\n## Özet\n\nKararlar...\n"


class Guard(unittest.TestCase):
    def test_the_shapes_found_in_the_vault_are_caught(self):
        bad = [
            FM.format("Write is disabled in this session, so I'll output the compiled summary") + "# x\n",
            FM.format("x") + "The Write tool is disabled for this session, so I'll output it.\n",
            FM.format("x") + "Here is the compiled status mirror for the project.\n",
            FM.format("x") + "Here is the compiled digest -- please approve the write when prompted.\n",
            FM.format("x") + "# x\n\nbody\n\nThe Write tool is disabled here so I couldn't save it.\n",
            FM.format("x") + "Per the instruction I output only the markdown for this source.\n",
            FM.format("x") + "Önceki oturumdan kalan not: bu oturumda Write tool devre disi olabilir.\n",
            FM.format("x") + "A preamble.\n\n---\nlang: tr\nsummary_en: the real page\n---\n# x\n",
        ]
        for text in bad:
            with self.subTest(text=text[60:120]):
                self.assertTrue(G.is_bad(text))

    def test_real_content_stays_legal(self):
        mid = GOOD + ("Filler sentence about decisions. " * 80) + \
            "\n\nThe team disabled the write tool in the CMS during the migration.\n" + \
            ("More filler about outcomes. " * 60)
        template = GOOD + "\n## Output format\n\n```markdown\n---\nlang: tr\nsummary_en: <one line>\n---\n```\n"
        for text in (GOOD, mid, template):
            with self.subTest(text=text[-80:]):
                self.assertEqual(G.problems(text), [])


class Wiring(unittest.TestCase):
    def test_the_compiler_refuses_to_write_chatter_and_keeps_the_old_page(self):
        with tempfile.TemporaryDirectory() as d, patch.object(compiler, "VAULT", Path(d)), \
                contextlib.redirect_stderr(io.StringIO()):
            dst = Path(d) / ".wiki/summaries/x.md"
            dst.parent.mkdir(parents=True)
            dst.write_text(GOOD)
            ok = compiler.write_compiled(dst, FM.format("Write is disabled, so I'll output it") + "# x\n", [])
            self.assertFalse(ok)
            self.assertEqual(dst.read_text(), GOOD)
            self.assertTrue(compiler.write_compiled(dst, GOOD, []))

    def test_the_concept_validator_rejects_chatter(self):
        bad = GOOD.replace("# Thinking in Bets", "Here is the updated page:\n\n# Thinking in Bets")
        self.assertTrue(any("chatter" in p for p in C.validate_update("", bad)))


if __name__ == "__main__":
    unittest.main()
