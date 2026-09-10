#!/usr/bin/env python3
"""Empty-transcript filter for the ingest side.

whisper.cpp does not return an empty string on silence or noise; it
hallucinates stock artifacts (Turkish large-v3 favourites: "Altyazı M.K."
subtitle credits, "Abone olmayı unutmayın", bracketed "[Müzik]" tags, or a
single token repeated). These are non-empty, so a bare `if not text` guard
lets them through and they land as junk notes in the vault.

`is_empty_transcript` returns True when a transcript carries no real content
and should be dropped before a note is written. It is deliberately
conservative: it never rejects on length alone, so a genuine one-word memo
("hastane") survives. Only literally-empty text, bracket/punctuation-only
text, a known silence artifact, or one short token repeated is dropped.

Add new hallucinations to ARTIFACT_PATTERNS as they show up.
"""
import re

# Matched against the normalised transcript (lowercased, brackets removed,
# punctuation stripped, whitespace collapsed) with fullmatch.
ARTIFACT_PATTERNS = [
    re.compile(r"altyazı.*"),                 # "Altyazı M.K." / "Altyazılar ..." credits
    re.compile(r"(kanala |lütfen )?abone ol.*"),  # "Abone olmayı unutmayın"
    re.compile(r"(please )?subscribe.*"),
    re.compile(r"thanks? for watching.*"),
]


def _normalise(text):
    """Lowercase, drop [..]/(..) tags, keep only letters/digits/space."""
    s = text.strip().lower()
    s = re.sub(r"\[.*?\]|\(.*?\)", " ", s)          # [Müzik], (applause)
    s = re.sub(r"[^0-9a-zçğıöşü\s]", " ", s)         # keep Turkish letters + digits
    return re.sub(r"\s+", " ", s).strip()


def is_empty_transcript(text):
    """True if the transcript has no usable content and should be skipped."""
    if not text or not text.strip():
        return True
    norm = _normalise(text)
    if not norm:                                    # only brackets/punctuation/symbols
        return True
    if any(p.fullmatch(norm) for p in ARTIFACT_PATTERNS):
        return True
    tokens = norm.split()                           # one short token repeated (>=3x)
    if len(tokens) >= 3 and len(set(tokens)) == 1:
        return True
    return False


if __name__ == "__main__":
    _drop = [
        "", "   ", ".", "...", "[Müzik]", "( applause )",
        "Altyazı M.K.", "altyazılar hukukunuz", "Abone olmayı unutmayın",
        "Kanala abone olun", "Please subscribe to my channel",
        "Thanks for watching!", "so so so so so",
    ]
    _keep = [
        "hastane", "Yarın Yakup ile sözleşmeyi imzala.",
        "Kardiyoloji randevusu bu hafta.", "Dünya için okul evrakı",
        "teşekkür ederim", "3 kutu süt al",
    ]
    ok = True
    for t in _drop:
        if not is_empty_transcript(t):
            print(f"FAIL drop: {t!r}"); ok = False
    for t in _keep:
        if is_empty_transcript(t):
            print(f"FAIL keep: {t!r}"); ok = False
    print("all ok" if ok else "FAILURES above")
    raise SystemExit(0 if ok else 1)
