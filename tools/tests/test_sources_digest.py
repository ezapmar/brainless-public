"""The compile stamp must not depend on where the vault sits.

Until 2026-10-08 sources_digest hashed the absolute path, so a compile on the
other machine, or in a moved checkout, saw every page as stale and rebuilt the
whole wiki. The digest now hashes the vault-relative POSIX path, and a page
stamped with the old digest still counts as current on the machine that wrote
it, so the change itself does not trigger a rebuild.
"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
import compile_resources as C


class SourcesDigestTests(unittest.TestCase):
    def vault(self, root):
        d = Path(root) / "Library"
        d.mkdir(parents=True)
        (d / "Dünya.md").write_text("same text", encoding="utf-8")
        (d / "b.md").write_text("other text", encoding="utf-8")
        return [d / "Dünya.md", d / "b.md"]

    def test_same_content_in_two_locations_hashes_the_same(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            pa, pb = self.vault(a), self.vault(b)
            with patch.object(C, "VAULT", Path(a)):
                da = C.sources_digest(pa, scope="x")
            with patch.object(C, "VAULT", Path(b)):
                db = C.sources_digest(pb, scope="x")
        self.assertEqual(da, db)

    def test_content_change_changes_the_digest(self):
        with tempfile.TemporaryDirectory() as a:
            paths = self.vault(a)
            with patch.object(C, "VAULT", Path(a)):
                before = C.sources_digest(paths)
                paths[1].write_text("edited", encoding="utf-8")
                self.assertNotEqual(before, C.sources_digest(paths))

    def test_legacy_absolute_path_stamp_still_matches_on_its_own_machine(self):
        import hashlib
        with tempfile.TemporaryDirectory() as a:
            paths = self.vault(a)
            h = hashlib.sha256()
            for p in sorted(paths, key=str):
                h.update(str(p).encode())
                h.update(p.read_bytes())
            legacy = h.hexdigest()[:12]
            with patch.object(C, "VAULT", Path(a)):
                self.assertNotEqual(legacy, C.sources_digest(paths))
                self.assertTrue(C.digest_matches(legacy, paths))
                self.assertTrue(C.digest_matches(C.sources_digest(paths), paths))
                self.assertFalse(C.digest_matches("000000000000", paths))
                self.assertFalse(C.digest_matches(None, paths))


if __name__ == "__main__":
    unittest.main()
