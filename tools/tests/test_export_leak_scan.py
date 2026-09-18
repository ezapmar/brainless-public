"""Offline regressions for the public export's leak scan.

The scan is the last gate before private material reaches the public repo, so
these tests pin what it must catch, what it must not flag, and what it must
skip. No network, no git.

Run: python3 -B -m unittest discover -s tools/tests -v
"""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("export_public", ROOT / "tools" / "export_public.py")
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)

# Fixtures are assembled at runtime so this file never contains a leak-shaped string:
# the public CI runs the same scan over the tests directory.
IDENTITY = "".join(str(i % 10) for i in range(1, 12))      # eleven digits
EMAIL = "someone" + "@" + "private-corp.test"
AWS_KEY = "AKIA" + "ABCDEFGHIJKLMNOP"
IBAN = "TR" + "12" * 12
OWNER_WORD = export.OWNER_WORDS[0]


class LeakScanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def write(self, relative, data):
        path = self.out / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            path.write_bytes(data)
        else:
            path.write_text(data, encoding="utf-8")
        return path

    def scan(self):
        return export.leak_scan(str(self.out))

    def labels(self, relative):
        return {label for r, label, _ in self.scan() if r == relative}

    # --- what it must catch -------------------------------------------------

    def test_clean_tree_has_no_findings(self):
        self.write("docs/a.md", "# Nothing here\n\nA plain document with a phone-free paragraph.\n")
        self.write("tools/x.py", "print('hello')\n")
        self.assertEqual(self.scan(), [])

    def test_planted_secrets_are_found_with_their_labels(self):
        self.write("docs/notes.md", f"id {IDENTITY}\nmail {EMAIL}\nkey {AWS_KEY}\niban {IBAN}\n")
        found = self.labels("docs/notes.md")
        for label in ("identity number (11 digits)", "e-mail address", "aws access key", "iban (TR)"):
            self.assertIn(label, found)

    def test_owner_name_is_flagged_outside_the_allow_list(self):
        self.write("tools/thing.py", f"# written by {OWNER_WORD}\n")
        self.write("tools/upper.py", f"# {OWNER_WORD.upper()} again\n")
        self.assertIn("owner reference", self.labels("tools/thing.py"))
        self.assertIn("owner reference", self.labels("tools/upper.py"), "match must be case-insensitive")

    def test_private_key_block_is_found(self):
        self.write("tools/k.pem", "-----BEGIN OPENSSH " + "PRIVATE KEY-----\nabc\n")
        self.assertIn("private key block", self.labels("tools/k.pem"))

    def test_every_hit_is_reported_not_just_the_first(self):
        self.write("docs/two.md", f"{IDENTITY}\n\nand later {IDENTITY[::-1]}\n")
        hits = [h for r, label, h in self.scan() if r == "docs/two.md" and label.startswith("identity")]
        self.assertEqual(len(hits), 2)

    # --- what it must not flag ----------------------------------------------

    def test_owner_name_is_allowed_where_the_allow_list_says_so(self):
        allowed = export.OWNER_ALLOW[0]
        self.write(allowed, f"Written by {OWNER_WORD}.\n")
        self.assertNotIn("owner reference", self.labels(allowed))

    def test_allow_list_waives_owner_words_only_never_secrets(self):
        allowed = export.OWNER_ALLOW[0]
        self.write(allowed, f"By {OWNER_WORD}. Token: {AWS_KEY}\n")
        self.assertIn("aws access key", self.labels(allowed))

    def test_documented_example_addresses_are_not_leaks(self):
        self.write("docs/a.md", "user@example.com noreply@github.com bot@report.spiky.ai\n")
        self.assertNotIn("e-mail address", self.labels("docs/a.md"))

    def test_numbers_inside_longer_runs_are_not_identity_numbers(self):
        self.write("docs/a.md", "order 123456789012 (twelve digits) and 1234567890 (ten)\n")
        self.assertNotIn("identity number (11 digits)", self.labels("docs/a.md"))

    def test_git_metadata_is_not_scanned(self):
        self.write(".git/config", f"{IDENTITY} {EMAIL}\n")
        self.assertEqual(self.scan(), [])

    # --- what it must skip, and what it must not ----------------------------

    def test_raster_images_are_skipped_even_when_their_bytes_look_like_leaks(self):
        noise = b"\x89PNG\r\n\x1a\n" + IDENTITY.encode() + b"\x00" + EMAIL.encode() + b"\xff" * 16
        for ext in export.BINARY_EXT:
            self.write(f"docs/assets/logo{ext}", noise)
        self.assertEqual(self.scan(), [])

    def test_svg_is_text_and_stays_in_the_scan(self):
        self.write("docs/assets/logo.svg", f'<svg><path transform="translate({IDENTITY},5)"/></svg>')
        self.assertIn("identity number (11 digits)", self.labels("docs/assets/logo.svg"))

    def test_binary_skip_matches_extension_case_insensitively(self):
        self.write("docs/assets/LOGO.PNG", IDENTITY.encode())
        self.assertEqual(self.scan(), [])

    # --- the ship list --------------------------------------------------------

    def test_shipped_directories_never_include_a_content_home(self):
        homes = {"Work", "Personal", "Library", "Thinking", "Inbox", "raw"}
        for d in export.SHIP_DIRS:
            self.assertNotIn(d.split("/")[0], homes, d)
        for f in export.SHIP_FILES:
            self.assertNotIn(f.split("/")[0], homes, f)


if __name__ == "__main__":
    unittest.main()
