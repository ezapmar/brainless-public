"""Language detection: the vault answers in the language it was spoken to."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import lang_detect  # noqa: E402
from lang_detect import detect, scores  # noqa: E402


class Detection(unittest.TestCase):
    def test_turkish_with_diacritics(self):
        code, conf = detect("Bu hafta Visma süreci için karar vermemiz gerekiyor "
                            "ama karşı taraftan bilgi gelmedi.")
        self.assertEqual(code, "tr")
        self.assertGreater(conf, 0.7)

    def test_turkish_without_diacritics(self):
        # Voice transcripts and phone keyboards often drop Turkish letters, so
        # the stopword signal has to carry the decision on its own.
        code, _ = detect("Yarin toplantiya katil ve sunumu hazirla, sonra ekibe gonder.")
        self.assertEqual(code, "tr")

    def test_english(self):
        code, conf = detect("We should decide whether to ship the package this "
                            "quarter or fix the internal issues first.")
        self.assertEqual(code, "en")
        self.assertGreater(conf, 0.7)

    def test_short_input_falls_back(self):
        code, conf = detect("ok", default="en")
        self.assertEqual(code, "en")
        self.assertEqual(conf, 0.0)
        self.assertEqual(detect("ok", default="tr")[0], "tr")

    def test_never_raises_on_junk(self):
        for junk in ("", None, "***", "123 456", "\x00\x01"):
            code, _ = detect(junk)
            self.assertIn(code, ("tr", "en"))

    def test_noise_is_stripped_before_counting(self):
        # Frontmatter, code and URLs are English-shaped boilerplate that would
        # otherwise drag a Turkish note towards English.
        noisy = ("---\nlang: tr\nsummary_en: the report is not ready and there "
                 "are three open items\n---\n"
                 "```\nfor x in the list of the items: print(x)\n```\n"
                 "https://www.example.com/the-report-of-the-year\n"
                 "Bu rapor hazır değil, önümüzde üç açık madde var ve karar verilmedi.")
        self.assertEqual(detect(noisy)[0], "tr")

    def test_scores_expose_reasoning(self):
        # u-umlaut and c-cedilla are deliberately NOT counted (they appear in
        # English loanwords and names), so the sentence must carry i-dotless,
        # s-cedilla or g-breve for the character signal to fire at all.
        s = scores("Bu cümlede sayıca yeterli kelime ve başka işaretler bulunur.")
        self.assertGreater(s["tr_chars"], 0)
        self.assertGreater(s["tokens"], 5)

    def test_self_test_corpus_passes(self):
        for want, text in lang_detect._SELF_TEST:
            self.assertEqual(detect(text)[0], want, msg=text[:50])


if __name__ == "__main__":
    unittest.main()
