"""Secret shapes the guards must catch. Fake values are assembled at run time
so this file itself never matches the patterns it tests."""
import unittest

import leak_patterns as lp


def labels(text):
    return {label for label, rx in lp.LEAK_PATTERNS if rx.search(text)}


class SecretShapes(unittest.TestCase):
    def test_anthropic_key(self):
        key = "sk-" + "ant-" + "api03-" + "A1b2C3d4E5f6G7h8I9j0" * 3
        self.assertIn("anthropic key", labels(key))

    def test_openai_project_key_with_dashes(self):
        key = "sk-" + "proj-" + "Ab_cd-Ef" * 6
        self.assertIn("openai key", labels(key))

    def test_google_oauth_client_secret(self):
        self.assertIn("google oauth secret", labels("GOC" + "SPX-" + "a1B2c3D4e5F6g7H8i9J0k1L2"))

    def test_uk_iban_compact_and_spaced(self):
        compact = "GB" + "29" + "NWBK" + "60161331926819"
        spaced = "GB" + "29 " + "NWBK " + "6016 1331 9268 19"
        self.assertIn("iban (GB)", labels(compact))
        self.assertIn("iban (GB)", labels(spaced))

    def test_ordinary_text_is_clean(self):
        self.assertFalse(labels("GB pounds, sk-ant mention, GOCSPX in prose") & {
            "anthropic key", "google oauth secret", "iban (GB)"})


if __name__ == "__main__":
    unittest.main()
