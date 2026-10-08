"""The generated script index in docs/scripts.md, kept honest.

docs/scripts.md drifted by 35 scripts before the index existed. The first test
fails the day a script is added, renamed or redescribed without running
`python3 tools/docs_index.py`; the rest pin how a description is taken.

Run: python3 -B -m unittest discover -s tools/tests -v
"""
import io
import contextlib
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import docs_index

DASH = chr(0x2014)


class IndexIsCurrent(unittest.TestCase):
    def test_block_in_docs_matches_the_scripts_on_disk(self):
        text = (ROOT / "docs" / "scripts.md").read_text(encoding="utf-8")
        block = docs_index.current_block(text)
        self.assertIsNotNone(block, "docs/scripts.md has no script index block")
        rendered = docs_index.render(ROOT)
        if block != rendered:
            self.fail("script index is stale, run python3 tools/docs_index.py:\n  "
                      + "\n  ".join(docs_index.diff_summary(block, rendered)))

    def test_tests_and_private_exports_are_not_listed(self):
        listed = docs_index.iter_scripts(ROOT)
        self.assertTrue(listed, "no scripts found")
        self.assertFalse([p for p in listed if p.startswith("tools/tests/")])
        self.assertFalse([p for p in listed if p.endswith("__init__.py")])
        self.assertFalse([p for p in listed if p.startswith("tools/finance/")],
                         "export_public excludes tools/finance, so docs/ must not name it")
        self.assertIn("tools/docs_index.py", listed)


class Describe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def write(self, name, text):
        p = self.dir / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_python_takes_the_docstring_first_line_trimmed_and_escaped(self):
        long = "x" * 200
        p = self.write("a.py", f'#!/usr/bin/env python3\n"""\n  Does a | b {DASH} and more.\n\n{long}\n"""\nimport os\n')
        self.assertEqual(docs_index.describe(p), "Does a \\| b, and more.")
        p = self.write("b.py", f'"""{long}"""\n')
        got = docs_index.describe(p)
        self.assertEqual(len(got), docs_index.MAX_DESC)
        self.assertTrue(got.endswith("..."))

    def test_python_without_docstring_falls_back_to_a_header_comment_only(self):
        p = self.write("c.py", "#!/usr/bin/env python3\n# Header line.\nimport os\n")
        self.assertEqual(docs_index.describe(p), "Header line.")
        p = self.write("d.py", "import os\n# Configuration\nX = 1\n")
        self.assertEqual(docs_index.describe(p), docs_index.NO_DESC)

    def test_shell_takes_the_first_comment_after_the_shebang(self):
        p = self.write("e.sh", "#!/bin/bash\n\n# shellcheck disable=SC2034\n#\n# Pull, run, push.\n# More.\nset -u\n")
        self.assertEqual(docs_index.describe(p), "Pull, run, push.")


class Rewrite(unittest.TestCase):
    def test_markers_are_appended_once_then_replaced_in_place(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "tools").mkdir()
            (root / "docs").mkdir()
            (root / "tools" / "one.py").write_text('"""One tool."""\n')
            doc = root / "docs" / "scripts.md"
            doc.write_text("# The scripts\n\nHand-written.\n")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(docs_index.main(["--check", "--root", str(root)]), 1)
                self.assertEqual(doc.read_text(), "# The scripts\n\nHand-written.\n", "--check writes nothing")
                docs_index.main(["--root", str(root)])
                first = doc.read_text()
                self.assertTrue(first.startswith("# The scripts\n\nHand-written.\n\n## Script index\n\n"))
                self.assertIn("| `tools/one.py` | One tool. |", first)
                (root / "tools" / "two.py").write_text('"""Two tool."""\n')
                self.assertEqual(docs_index.main(["--check", "--root", str(root)]), 1)
                self.assertIn("added: tools/two.py", out.getvalue())
                docs_index.main(["--root", str(root)])
                self.assertEqual(docs_index.main(["--check", "--root", str(root)]), 0)
            second = doc.read_text()
            self.assertEqual(second.count(docs_index.START), 1)
            self.assertEqual(second.count("## Script index"), 1)
            self.assertIn("| `tools/two.py` | Two tool. |", second)


if __name__ == "__main__":
    unittest.main()
