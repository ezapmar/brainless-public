"""The cloud mask: what a cloud provider receives has no personal data.

Tested from the receiving side: the dispatcher records the exact prompt each
provider got. All values are made up.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import llm
import redact
from test_llm_lanes import Env

LOCAL = "http://127.0.0.1:8080/v1"
NOTE = ("Toplantı notu: Ayşe (ayse.yilmaz@ornekfirma.com.tr, +90 532 000 00 00) ve "
        "Mehmet (mehmet.kaya@ornekfirma.com.tr). Ayşe'ye tekrar ayse.yilmaz@ornekfirma.com.tr.")


class RedactForCloud(unittest.TestCase):
    def test_same_value_same_placeholder(self):
        out, counts = redact.redact_for_cloud(NOTE)
        self.assertEqual(counts, {"email": 3, "phone": 1})
        self.assertEqual(out.count("[e-posta 1]"), 2)
        self.assertEqual(out.count("[e-posta 2]"), 1)
        self.assertIn("[telefon 1]", out)
        self.assertNotIn("@ornekfirma", out)
        self.assertEqual(redact.find_personal(out), [])

    def test_clean_text_is_untouched(self):
        text = "Karar: 2027 teklifi 45.656 TL, destek@ornekfirma.com.tr adresine."
        self.assertEqual(redact.redact_for_cloud(text), (text, {}))

    def test_secret_is_masked(self):
        fake = "ghp_" + "a1B2" * 9
        out, counts = redact.redact_for_cloud(f"token {fake} burada")
        self.assertEqual(counts, {"github-token": 1})
        self.assertNotIn(fake, out)


class ProviderSeesMaskedPrompt(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        for name, attr in (("llm_log", "LOG_FILE"), ("llm_status", "STATUS_FILE"),
                           ("llm_costs.jsonl", "COST_FILE"), ("llm_budget.json", "BUDGET_FILE"),
                           ("egress.jsonl", "EGRESS_FILE")):
            p = mock.patch.object(llm, attr, str(Path(d.name) / name))
            p.start()
            self.addCleanup(p.stop)
        self.got = []
        self.answer = None

        def fake(provider, prompt, *a, **k):
            self.got.append((provider, prompt))
            return self.answer
        p = mock.patch.object(llm, "_dispatch", side_effect=fake)
        p.start()
        self.addCleanup(p.stop)

    def test_cloud_gets_the_mask(self):
        self.answer = "ok"
        with Env(BRAINLESS_LLM_PROVIDER_COMPILE="claude-cli", BRAINLESS_LLM_LOCAL_ONLY="",
                 BRAINLESS_LLM_REDACT=None):
            llm.run_prompt(NOTE, lane="compile")
        provider, prompt = self.got[0]
        self.assertEqual(provider, "claude-cli")
        self.assertNotIn("@ornekfirma", prompt)
        self.assertNotIn("532 000", prompt)

    def test_local_gets_the_text(self):
        self.answer = "ok"
        with Env(BRAINLESS_LLM_PROVIDER_COMPILE="openai-compatible", BRAINLESS_LLM_BASE_URL=LOCAL,
                 BRAINLESS_LLM_LOCAL_ONLY=""):
            llm.run_prompt(NOTE, lane="compile")
        self.assertEqual(self.got, [("openai-compatible", NOTE)])

    def test_cloud_fallback_after_local_failure_is_masked(self):
        with Env(BRAINLESS_LLM_PROVIDER_COMPILE="openai-compatible", BRAINLESS_LLM_BASE_URL=LOCAL,
                 BRAINLESS_LLM_FALLBACK_COMPILE="claude-cli", BRAINLESS_LLM_ALLOW_CLOUD_FALLBACK="1",
                 BRAINLESS_LLM_LOCAL_ONLY="", BRAINLESS_LLM_REDACT=None):
            llm.run_prompt(NOTE, lane="compile")
        self.assertEqual([p for p, _ in self.got], ["openai-compatible", "claude-cli"])
        self.assertEqual(self.got[0][1], NOTE)
        self.assertNotIn("@ornekfirma", self.got[1][1])

    def test_remote_openai_compatible_is_cloud(self):
        with Env(BRAINLESS_LLM_PROVIDER_COMPILE="openai-compatible",
                 BRAINLESS_LLM_BASE_URL="https://api.example.com/v1", BRAINLESS_LLM_LOCAL_ONLY=""):
            llm.run_prompt(NOTE, lane="compile")
        self.assertNotIn("@ornekfirma", self.got[0][1])


class SmartProcessorRead(unittest.TestCase):
    def test_personal_file_goes_inline_not_through_read(self):
        import smart_processor as sp
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
            fh.write(NOTE)
        self.addCleanup(Path(fh.name).unlink)
        with Env(BRAINLESS_LLM_PROVIDER_SMART_SUMMARY="claude-cli"):
            block, tools = sp._source_block(fh.name, "smart-summary")
        self.assertIsNone(tools)
        self.assertIn("<<<DOC", block)

    def test_clean_file_keeps_the_read_path(self):
        import smart_processor as sp
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
            fh.write("# Bir kitap\n\nTez: hız bir tercihtir.")
        self.addCleanup(Path(fh.name).unlink)
        with Env(BRAINLESS_LLM_PROVIDER_SMART_SUMMARY="claude-cli"):
            block, tools = sp._source_block(fh.name, "smart-summary")
        self.assertEqual(tools, ["Read"])


if __name__ == "__main__":
    unittest.main()
