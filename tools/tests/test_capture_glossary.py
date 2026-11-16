"""Offline regressions for the capture glossary and the raw transcript block.

Voice notes came out with the company name and everyday words misheard,
because the cleanup step saw only the head of CONTEXT.md and whisper got no
hint at all. The glossary file now feeds both, and every voice note keeps
whisper's own words in a folded callout so a misheard name can be checked.
Nothing here calls whisper or an LLM.

Run: python3 -B -m unittest discover -s tools/tests -v
"""
import atexit
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

_IMPORT_VAULT = tempfile.mkdtemp(prefix="brainless-capture-glossary-")
atexit.register(shutil.rmtree, _IMPORT_VAULT, True)
_OLD_VAULT = os.environ.get("BRAINLESS_VAULT")
os.environ["BRAINLESS_VAULT"] = _IMPORT_VAULT
try:
    import telegram_capture as capture
finally:
    if _OLD_VAULT is None:
        os.environ.pop("BRAINLESS_VAULT", None)
    else:
        os.environ["BRAINLESS_VAULT"] = _OLD_VAULT

GLOSSARY = """# Capture glossary

> Header lines like this one are skipped.

## Terms

- Acme Payroll
- timesheet
Acme Payroll

## Known mishearings

- Akmi -> Acme Payroll
- time seat -> timesheet
- broken ->
"""


class GlossaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "capture-glossary.md"
        self.path.write_text(GLOSSARY, encoding="utf-8")

    def test_terms_and_corrections(self):
        terms, corrections = capture.load_glossary(str(self.path))
        self.assertEqual(terms, ["Acme Payroll", "timesheet"])
        self.assertEqual(corrections, [("Akmi", "Acme Payroll"), ("time seat", "timesheet")])

    def test_missing_file_is_empty(self):
        self.assertEqual(capture.load_glossary(str(self.path) + ".gone"), ([], []))

    def test_whisper_prompt_stops_at_the_cap(self):
        terms = [f"term{i:03d}" for i in range(500)]
        prompt = capture.whisper_prompt(terms)
        self.assertLessEqual(len(prompt), capture.WHISPER_PROMPT_CHARS)
        self.assertTrue(prompt.startswith("term000, term001"))
        self.assertEqual(capture.whisper_prompt([]), "")

    def test_cleanup_prompt_carries_the_glossary(self):
        seen = {}
        with patch.object(capture, "GLOSSARY_FILE", str(self.path)), \
                patch.object(capture, "run_prompt", side_effect=lambda p, **kw: seen.setdefault("p", p)):
            capture.make_note("akmi time seat", "voice note")
        self.assertIn("- Acme Payroll", seen["p"])
        self.assertIn('"time seat" is a mishearing of "timesheet"', seen["p"])

    def test_transcribe_passes_the_prompt_to_whisper(self):
        calls = []

        class Done:
            returncode, stdout, stderr = 0, "metin", ""

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return Done()

        def fake_download(url, dest):
            Path(dest).write_bytes(b"")

        info = {"result": {"file_path": "voice/1.oga", "file_size": 10}}
        with patch.object(capture, "GLOSSARY_FILE", str(self.path)), \
                patch.object(capture, "api", return_value=info), \
                patch.object(capture.urllib.request, "urlretrieve", side_effect=fake_download), \
                patch.object(capture.subprocess, "run", side_effect=fake_run):
            self.assertEqual(capture.transcribe("token", "file"), "metin")
        whisper = calls[-1]
        self.assertIn("--prompt", whisper)
        self.assertEqual(whisper[whisper.index("--prompt") + 1], "Acme Payroll, timesheet")

    def test_no_glossary_no_prompt_flag(self):
        with patch.object(capture, "GLOSSARY_FILE", str(self.path) + ".gone"):
            self.assertEqual(capture.whisper_prompt(capture.load_glossary()[0]), "")


class RawTranscriptTest(unittest.TestCase):
    def test_folded_callout_keeps_every_line(self):
        block = capture.raw_transcript_block("first line\n\nsecond line\n")
        lines = block.splitlines()
        self.assertTrue(lines[0].startswith("> [!quote]- "))
        self.assertEqual(lines[1:], ["> first line", ">", "> second line"])


if __name__ == "__main__":
    unittest.main()
