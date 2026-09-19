"""The privacy boundary, tested instead of trusted.

A .gitignore rule that names one path dies the day the path is renamed, and it
dies silently: nothing errors, the next compile simply tracks the file. That
happened twice in one afternoon on 2026-09-19. A 235 MB PDF, ignored by name,
was renamed by the document processor and bounced the push. The same class of
break on a finance or a case-file home would not have bounced anything; it
would have committed bank details and named people.

So these tests assert the outcome, not the rule text: the protected paths are
ignored right now, and nothing protected is tracked right now. Rename a folder
and the first test fails. Commit something sensitive and the second one does.

These call git, so they are integration tests, not unit tests. That is the
point: the thing under test is what git actually does with the rules, and a
reimplementation of gitignore matching here would be testing the wrong thing.
"""
from pathlib import Path
import re
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from owner_profile import (  # noqa: E402
    GITIGNORE_NETS, PROTECTED_HOMES, private_segment_patterns)

# Every home that must never reach the remote. The list is deployment data and
# lives in PROFILE.md, which is private: naming these folders here would put the
# very things they protect into a public repository.
PROTECTED = PROTECTED_HOMES

# Patterns that must never appear in `git ls-files`. Written against the path
# shapes the vault actually produces, including the compiled layer's flattened
# names, where a folder rule does not reach.
FORBIDDEN_TRACKED = [
    r"Official Docs/",
    r"/[^/]+ - Health/",
    r"(?i)passport|pasaport|schengen|kimlik|n[uü]fus",
    r"(?i)security incidents/",
    r"(?i)finance[_/]resources",
] + private_segment_patterns()

# The pattern nets added on 2026-09-19. Each one is the backup for a rule that
# names a single path; losing one puts that home back on a single point of
# failure, which is why their absence is a test failure and not a preference.
REQUIRED_PATTERNS = list(GITIGNORE_NETS)


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                          text=True, check=False)


class TestProtectedPathsAreIgnored(unittest.TestCase):
    def test_each_protected_home_exists_and_is_ignored(self):
        for rel in PROTECTED:
            with self.subTest(path=rel):
                path = ROOT / rel
                self.assertTrue(
                    path.exists(),
                    f"{rel} is gone. Either the data moved, and the rule now "
                    f"protects nothing, or the PROFILE.md entry is stale. Both "
                    f"need a look.")
                result = git("check-ignore", "-v", rel)
                self.assertEqual(
                    result.returncode, 0,
                    f"{rel} is NOT ignored. A rename probably broke its rule; "
                    f"fix .gitignore before committing anything.")


class TestNothingSensitiveIsTracked(unittest.TestCase):
    def test_no_forbidden_path_is_tracked(self):
        listing = git("ls-files").stdout
        for pattern in FORBIDDEN_TRACKED:
            with self.subTest(pattern=pattern):
                hits = [ln for ln in listing.splitlines() if re.search(pattern, ln)]
                self.assertEqual(
                    hits, [],
                    f"tracked files match {pattern!r}: {hits[:3]}. Ignoring them "
                    f"now does nothing, since .gitignore does not untrack; they "
                    f"need git rm --cached and a look at the history.")


class TestPatternNetsSurvive(unittest.TestCase):
    def test_every_backup_pattern_is_still_present(self):
        rules = (ROOT / ".gitignore").read_text().splitlines()
        for pattern in REQUIRED_PATTERNS:
            with self.subTest(pattern=pattern):
                self.assertTrue(
                    pattern in rules,
                    f"{pattern} was removed from .gitignore. It is the second "
                    f"net under a rule that names one path; without it a single "
                    f"rename exposes that home.")


if __name__ == "__main__":
    unittest.main()
