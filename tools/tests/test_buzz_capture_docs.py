"""Documents sent to Buzz #inbox, without the relay.

What must hold: a PDF or office file is recognised by MIME, URL or imeta
filename; it becomes one markdown note under Inbox/Documents; the original
never lands in the vault; a file named like a private list is refused before
download. The relay download is a mock; markitdown runs for real when present.

Run: python3 -B -m unittest tools.tests.test_buzz_capture_docs -v
"""
import importlib.util
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("BRAINLESS_VAULT", str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / ".agents/scripts"))
import buzz_capture as bc  # noqa: E402

URL = "https://buzz.example/media/abc123"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
HAS_MARKITDOWN = all(importlib.util.find_spec(m) for m in ("markitdown", "openpyxl"))


def file_msg(name, mime, url=URL + ".xlsx", caption=""):
    body = f"[{name}]({url})" + (f"\n{caption}" if caption else "")
    return {"content": body, "tags": [["imeta", f"url {url}", f"m {mime}", f"filename {name}"]]}


class Classify(unittest.TestCase):
    def test_by_mime(self):
        self.assertEqual(bc.classify(URL, "application/pdf"), ("document", ".pdf"))

    def test_by_url_extension(self):
        self.assertEqual(bc.classify(URL + ".docx", None), ("document", ".docx"))

    def test_by_imeta_filename(self):
        self.assertEqual(bc.classify(URL, "application/octet-stream", "deck.pptx"), ("document", ".pptx"))

    def test_images_and_audio_unchanged(self):
        self.assertEqual(bc.classify(URL + ".png", "image/png")[0], "image")
        self.assertEqual(bc.classify(URL + ".m4a", None)[0], "audio")
        self.assertEqual(bc.classify(URL + ".zip", "application/zip")[0], "other")

    def test_filename_read_from_imeta(self):
        self.assertEqual(bc.media_from_message(file_msg("Q3.xlsx", XLSX))[0][2], "Q3.xlsx")

    def test_slug_keeps_only_plain_hyphens(self):
        self.assertEqual(bc.doc_slug(f"Nakit {chr(0x2013)} Senaryosu (v2).xlsx"), "Nakit-Senaryosu-v2")


@unittest.skipUnless(HAS_MARKITDOWN, "markitdown not installed")
class HandleDocument(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name) / "vault"
        self.vault.mkdir()
        import openpyxl
        self.src = Path(self.tmp.name) / "src.xlsx"
        wb = openpyxl.Workbook()
        wb.active["A1"] = "cash floor"
        wb.save(self.src)

    def fake_download(self, url, dest):
        shutil.copy(self.src, dest)
        return True

    def test_converts_into_inbox_documents_without_the_original(self):
        with patch.object(bc, "VAULT", str(self.vault)), \
             patch.object(bc, "download", side_effect=self.fake_download):
            title, path = bc.handle(file_msg("Q3 plan.xlsx", XLSX, caption="for Friday"))
        p = Path(path)
        self.assertEqual(p.parent, self.vault / "Inbox" / "Documents")
        text = p.read_text(encoding="utf-8")
        self.assertEqual(title, "for Friday")
        self.assertIn("cash floor", text)
        self.assertNotIn("/media/", text)
        self.assertEqual([f for f in self.vault.rglob("*.xlsx")], [])

    def test_private_name_refused_before_download(self):
        with patch.object(bc, "VAULT", str(self.vault)), \
             patch.object(bc, "PRIVATE_NAME_PARTS", ("Employee List",)), \
             patch.object(bc, "download") as dl:
            with self.assertRaises(RuntimeError):
                bc.handle(file_msg("Employee List Apr 2026.xlsx", XLSX))
        dl.assert_not_called()
        self.assertFalse((self.vault / "Inbox").exists())


if __name__ == "__main__":
    unittest.main()
