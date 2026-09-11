#!/usr/bin/env python3
"""Empty-transcript filter for the ingest side.

whisper.cpp does not return an empty string on silence or noise; it
hallucinates stock artifacts (large-v3 favourites: subtitle credits, "do not
forget to subscribe", bracketed "[Music]" tags, or a single token repeated).
These are non-empty, so a bare `if not text` guard lets them through and they
land as junk notes in the vault.

`is_empty_transcript` returns True when a transcript carries no real content
and should be dropped before a note is written. It is deliberately
conservative: it never rejects on length alone, so a genuine one-word memo
survives. Only literally-empty text, bracket/punctuation-only text, a known
silence artifact, or one short token repeated is dropped.

The artifact patterns are per language (the audio language of the owner) and
live in tools/locale/<lang>/transcript_filter.json; the current language and
English are merged. Add new hallucinations there as they show up.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from i18n import t_list  # noqa: E402

# Matched against the normalised transcript (lowercased, brackets removed,
# punctuation stripped, whitespace collapsed) with fullmatch.
ARTIFACT_PATTERNS = [re.compile(p) for p in t_list("transcript_filter.artifact_patterns")]


def _normalise(text):
    """Lowercase, drop [..]/(..) tags, keep only letters/digits/space."""
    s = text.strip().lower()
    s = re.sub(r"\[.*?\]|\(.*?\)", " ", s)          # [Music], (applause)
    s = re.sub(r"[^0-9a-zçğıöşü\s]", " ", s)         # keep Latin plus Turkish-alphabet letters and digits
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
    # Fixtures are per language too (same locale file), merged like the patterns.
    _drop = t_list("transcript_filter.selftest_drop")
    _keep = t_list("transcript_filter.selftest_keep")
    ok = True
    for t in _drop:
        if not is_empty_transcript(t):
            print(f"FAIL drop: {t!r}"); ok = False
    for t in _keep:
        if is_empty_transcript(t):
            print(f"FAIL keep: {t!r}"); ok = False
    print("all ok" if ok else "FAILURES above")
    raise SystemExit(0 if ok else 1)
