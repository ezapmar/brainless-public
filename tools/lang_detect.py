#!/usr/bin/env python3
"""Deterministic Turkish/English detection for a single piece of text.

Why this exists: the vault had exactly one language decision point,
owner_profile.LANG, resolved once at import from PROFILE.md. That is right for
"which language does this vault write in" but wrong for "which language is THIS
note in". A Turkish voice note and an English article should get answers in
their own language; dialectic.py used to force English on both.

No model, no network, stdlib only: this runs inside the classifier's hot path on
the worker and must cost nothing. Two independent signals are combined:

  1. Turkish-only characters (i-dotless, s-cedilla, g-breve, capital I-dot).
     Strong evidence, near-zero false positives in English text.
  2. Stopword hit rate for each language over the first N tokens.

Ambiguous or too-short input falls back to owner_profile.LANG, so the caller
always gets a usable code and never has to handle "unknown".

    from lang_detect import detect
    code, confidence = detect(note_text)     # ("tr", 0.91)
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from owner_profile import LANG  # noqa: E402

# Characters that appear in Turkish and effectively never in English prose.
# u-umlaut, o-umlaut and c-cedilla are deliberately excluded: they show up in
# loanwords and names (Zurich, facade, Mueller) and would over-trigger.
_TR_CHARS = set("ışğİĞŞIı")
_TR_ONLY = re.compile(r"[ışğİĞŞı]")

_TR_STOP = {
    "ve", "bir", "bu", "için", "ile", "da", "de", "mi", "ne", "çok", "daha",
    "olarak", "gibi", "ama", "ki", "var", "yok", "en", "o", "şu", "her",
    "sonra", "önce", "kadar", "değil", "olan", "diye", "ben", "biz", "bunu",
    "şey", "zaman", "üzerine", "göre", "hem", "ya", "yani", "ise", "mı",
}
_EN_STOP = {
    "the", "and", "of", "to", "a", "in", "is", "it", "that", "for", "with",
    "on", "as", "are", "this", "be", "at", "by", "not", "from", "or", "an",
    "we", "you", "they", "have", "has", "was", "were", "but", "if", "can",
    "will", "would", "should", "there", "their", "what", "which", "about",
}

# Below this many tokens the stopword rate is noise; fall back to the profile.
_MIN_TOKENS = 6
_TOKEN_CAP = 400
_TOKEN = re.compile(r"[^\W\d_]+", re.UNICODE)


def _strip_noise(text: str) -> str:
    """Drop the parts of a note that carry no language signal and skew counts:
    frontmatter, code fences, URLs, wikilink targets, hashtags."""
    text = re.sub(r"^---\s*\n.*?\n---\s*\n", "", text, flags=re.S)
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`[^`]*`", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\[\[([^\]|]*)\|?[^\]]*\]\]", r" \1 ", text)
    text = re.sub(r"#[\w/-]+", " ", text)
    return text


def scores(text: str) -> dict:
    """Raw signal values, exposed so tests and --explain can see the reasoning."""
    clean = _strip_noise(text or "")
    tokens = [m.group(0).lower() for m in _TOKEN.finditer(clean)][:_TOKEN_CAP]
    total = len(tokens)
    tr_hits = sum(1 for tok in tokens if tok in _TR_STOP)
    en_hits = sum(1 for tok in tokens if tok in _EN_STOP)
    tr_chars = len(_TR_ONLY.findall(clean))
    return {
        "tokens": total,
        "tr_stop": tr_hits,
        "en_stop": en_hits,
        "tr_stop_rate": (tr_hits / total) if total else 0.0,
        "en_stop_rate": (en_hits / total) if total else 0.0,
        "tr_chars": tr_chars,
        "tr_char_rate": (tr_chars / len(clean)) if clean else 0.0,
    }


def detect(text: str, default: str | None = None) -> tuple[str, float]:
    """Return (lang_code, confidence in 0..1). Never raises, never returns None.

    default: the code to fall back to when the text is too short or the signals
    disagree. Defaults to the vault's output language.
    """
    fallback = (default or LANG or "en").lower()
    s = scores(text)
    if s["tokens"] < _MIN_TOKENS:
        return fallback, 0.0

    # Turkish-only letters are the strongest evidence available. A handful of
    # them in a paragraph settles it; English prose produces zero.
    if s["tr_char_rate"] >= 0.004 or s["tr_chars"] >= 3:
        return "tr", min(0.99, 0.75 + s["tr_char_rate"] * 40 + s["tr_stop_rate"])

    tr, en = s["tr_stop_rate"], s["en_stop_rate"]
    if tr == 0 and en == 0:
        return fallback, 0.0
    if en > tr * 1.5:
        return "en", min(0.95, 0.5 + (en - tr) * 3)
    if tr > en * 1.5:
        return "tr", min(0.95, 0.5 + (tr - en) * 3)
    return fallback, 0.2


_SELF_TEST = [
    ("tr", "Bu hafta Visma süreci için bir karar vermemiz gerekiyor ama "
           "karşı taraftan hâlâ bilgi gelmedi ve süreç uzuyor."),
    ("tr", "Yarin toplantiya katil ve sunumu hazirla, sonra ekibe gonder."),
    ("en", "We should decide whether to ship the communication package this "
           "quarter or fix the internal issues first, because the data is thin."),
    ("en", "Call the supplier and confirm the delivery date for next week."),
    ("tr", "# Haftalık bağlılık anketi\n\nAkademik ortaklık için üniversite ile "
           "görüşmeyi planla, lisans gelmeden pilot başlamaz."),
    ("en", "The report is not ready. There are three open items that block it."),
]


def _self_test() -> int:
    bad = 0
    for want, text in _SELF_TEST:
        got, conf = detect(text)
        ok = got == want
        bad += 0 if ok else 1
        print(f"{'ok ' if ok else 'FAIL'} want={want} got={got} conf={conf:.2f} :: {text[:60]!r}")
    print(f"\n{len(_SELF_TEST) - bad}/{len(_SELF_TEST)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] == "--self-test":
        sys.exit(_self_test())
    if args[0] == "--explain":
        blob = open(args[1], errors="replace").read() if len(args) > 1 else sys.stdin.read()
        code, conf = detect(blob)
        print(f"lang={code} confidence={conf:.2f}")
        for k, v in scores(blob).items():
            print(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")
        sys.exit(0)
    code, conf = detect(" ".join(args))
    print(f"{code}\t{conf:.2f}")
