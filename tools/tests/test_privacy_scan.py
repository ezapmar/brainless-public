"""privacy_scan: the patterns, and the nightly gate that keeps a leak local.

All values here are made up, and the IBAN-shaped ones are assembled at run time
so the file itself carries no IBAN shape (the write hook refuses those). The
gate test runs against a throwaway git repo, never the vault.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import privacy_scan as ps


def make_tckn(first9: str) -> str:
    d = [int(c) for c in first9]
    d10 = (sum(d[0:9:2]) * 7 - sum(d[1:8:2])) % 10
    d11 = (sum(d) + d10) % 10
    return first9 + str(d10) + str(d11)


FAKE_TCKN = make_tckn("234567891")
FAKE_TR_IBAN = " ".join(["TR" + "00", "0000", "0000", "0000", "0000", "0000", "00"])
FAKE_GB_IBAN = " ".join(["GB" + "00", "ABCD", "0000", "0000", "0000", "00"])


def kinds(text):
    return sorted(k for k, _v, _s in ps.find_personal(text))


class Patterns(unittest.TestCase):
    def test_clean_page_passes(self):
        self.assertEqual(kinds("# Toplantı\n\nKarar: fiyat 45.656 TL, 2027 teklifi. Sipariş 20261121001."), [])

    def test_personal_email_caught_role_mailbox_not(self):
        self.assertEqual(kinds("yaz: ayse.yilmaz@ornekfirma.com.tr"), ["email"])
        self.assertEqual(kinds("destek@ornekfirma.com.tr, info@ornek.co.uk, noreply@ornek.com"), [])
        self.assertEqual(kinds("![logo](logo@2x.png) user@example.com"), [])

    def test_phone_shapes(self):
        for s in ("+90 532 000 00 00", "+905320000000", "0532 000 00 00", "+44 7700 900 123",
                  "07700 900123", "+90 (212) 000 00 00"):
            self.assertEqual(kinds(f"tel: {s}."), ["phone"], s)

    def test_numbers_that_are_not_phones(self):
        self.assertEqual(kinds("2026-11-21, 3.650 dosya, ISO 27001, 45656 TL"), [])

    def test_iban(self):
        self.assertEqual(kinds(FAKE_TR_IBAN), ["iban"])
        self.assertEqual(kinds(FAKE_GB_IBAN), ["iban"])

    def test_tckn_needs_a_valid_checksum(self):
        self.assertEqual(kinds(f"kimlik {FAKE_TCKN}"), ["tckn"])
        bad = FAKE_TCKN[:-1] + str((int(FAKE_TCKN[-1]) + 1) % 10)
        self.assertEqual(kinds(f"fatura {bad}"), [])
        self.assertEqual(kinds("test değeri 12345678950"), [])

    def test_secrets(self):
        fake = "ghp_" + "a1B2" * 9
        self.assertEqual([k for k, _v, _s in ps.find_secrets(f"token={fake}")], ["github-token"])

    def test_redact(self):
        text, n = ps.redact("Ayşe: ayse.yilmaz@ornekfirma.com.tr, +90 532 000 00 00")
        self.assertEqual(n, 2)
        self.assertNotIn("@ornekfirma", text)
        self.assertIn("[e-posta]", text)
        self.assertIn("[telefon]", text)


class AddedLines(unittest.TestCase):
    def test_only_plus_lines_of_each_file(self):
        diff = ("diff --git a/.wiki/a.md b/.wiki/a.md\n--- a/.wiki/a.md\n+++ b/.wiki/a.md\n"
                "@@ -1 +1 @@\n-old x@y.com\n+new line\n"
                "diff --git a/gone.md b/gone.md\n--- a/gone.md\n+++ /dev/null\n@@ -1 +0,0 @@\n-bye\n")
        self.assertEqual(ps.added_lines(diff), {".wiki/a.md": ["new line"]})

    def test_personal_data_gates_wiki_only_secrets_everywhere(self):
        fake = "ghp_" + "a1B2" * 9
        held = ps.gate_findings({".wiki/s.md": ["ayse.yilmaz@ornekfirma.com.tr"],
                                 "Work/kisi.md": ["ayse.yilmaz@ornekfirma.com.tr"],
                                 "tools/x.py": [f"T = '{fake}'"]})
        self.assertEqual(held, {".wiki/s.md": ["email"], "tools/x.py": ["github-token"]})


class Gate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
        self.git = lambda *a: subprocess.run(["git", *a], cwd=self.repo, env=env, check=True,
                                             capture_output=True, text=True).stdout
        self.git("init", "-q")
        (self.repo / ".wiki").mkdir()
        old = self.repo / ".wiki" / "old.md"
        old.write_text("eski: eski.kisi@ornekfirma.com.tr\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "base")
        for attr, val in (("VAULT", str(self.repo)), ("GATE_FILE", str(self.repo / "gate.json"))):
            p = mock.patch.object(ps, attr, val)
            p.start()
            self.addCleanup(p.stop)

    def staged(self):
        return set(self.git("diff", "--cached", "--name-only").split())

    def test_new_leak_is_unstaged_the_rest_commits(self):
        (self.repo / ".wiki" / "new.md").write_text("tel +90 532 000 00 00\n", encoding="utf-8")
        (self.repo / ".wiki" / "clean.md").write_text("temiz sayfa\n", encoding="utf-8")
        self.git("add", "-A")
        res = ps.run_gate()
        self.assertEqual(res["held"], {".wiki/new.md": ["phone"]})
        self.assertEqual(self.staged(), {".wiki/clean.md"})
        self.assertTrue((self.repo / ".wiki" / "new.md").exists())  # kept on disk, just not committed

    def test_old_finding_on_an_unchanged_line_does_not_block(self):
        old = self.repo / ".wiki" / "old.md"
        old.write_text(old.read_text(encoding="utf-8") + "yeni temiz satır\n", encoding="utf-8")
        self.git("add", "-A")
        self.assertEqual(ps.run_gate()["held"], {})
        self.assertEqual(self.staged(), {".wiki/old.md"})

    def test_dry_run_unstages_nothing(self):
        (self.repo / ".wiki" / "new.md").write_text("ayse.yilmaz@ornekfirma.com.tr\n", encoding="utf-8")
        self.git("add", "-A")
        self.assertIn(".wiki/new.md", ps.run_gate(unstage=False)["held"])
        self.assertEqual(self.staged(), {".wiki/new.md"})


class HealthRow(unittest.TestCase):
    def setUp(self):
        import health_check
        self.f = health_check.privacy_findings

    def rep(self, n):
        return {"files": n, "kinds": {"email": n} if n else {}}

    def test_levels(self):
        base = {"files": 40}
        self.assertEqual(self.f(self.rep(0), base, None)[0], "OK")
        self.assertEqual(self.f(self.rep(40), base, None)[0], "WARN")
        self.assertEqual(self.f(self.rep(41), base, None)[0], "RED")
        self.assertEqual(self.f(self.rep(3), None, None)[0], "WARN")

    def test_held_files_show_even_when_clean(self):
        status, _detail = self.f(self.rep(0), {"files": 0}, {"date": "2026-11-21 23:40", "held": {"x.md": ["email"]}})
        self.assertEqual(status, "WARN")


if __name__ == "__main__":
    unittest.main()
