#!/usr/bin/env python3
"""Near-duplicate check for task titles, shared by every writer of TASKS.md.

Two jobs append to the ledger: spiky_actions.py from meeting reports and
nightly_processor.py from the day's captures. The first had this guard, the
second matched titles character for character, so a promise rephrased by the
model on a later day became a second task, then a third. 130 open rows on
2026-09-19, many of them the same work in different words. One guard, one
module, both writers.
"""
import re

# Words that carry no meaning for "is this the same task", in both languages of
# the vault. Without them "Ozan ile konus" and "Ozan ile konusulacak" score as
# different tasks and the ledger grows a near-duplicate every week.
_STOP = {"ve", "ile", "icin", "bir", "bu", "su", "o", "de", "da", "ki", "mi",
         "the", "a", "an", "to", "for", "of", "and", "with", "on", "in", "is"}
DUPLICATE_OVERLAP = 0.6

STEM_LEN = 5


def _words(text):
    """Lower-case content stems, Turkish characters folded.

    Two foldings, both needed. Diacritics go because a transcript and a model
    disagree about them. Words are then cut to their first five characters,
    because Turkish is agglutinative: "entegrasyonu" and "entegrasyonunu" are
    the same word wearing different suffixes, and whole-word matching would
    call the same task new every week.
    """
    folded = (text or "").lower()
    for a, b in (("ı", "i"), ("ş", "s"), ("ğ", "g"), ("ü", "u"), ("ö", "o"), ("ç", "c")):
        folded = folded.replace(a, b)
    return {w[:STEM_LEN] for w in re.findall(r"[a-z0-9]+", folded)
            if w not in _STOP and len(w) > 2}


def is_duplicate(task, existing):
    """True when the ledger already holds this task in different words.

    The prompt asks the model not to repeat itself, and a large model obeys.
    A small local one does not always, and a duplicated promise is worse than
    a missed one: it reaches Google Tasks and then a person. So the guard is
    deterministic and sits after the model, where instructions cannot reach it.
    """
    new = _words(task)
    if not new:
        return True  # nothing but stop words is not a task
    for old in existing:
        prev = _words(old)
        if not prev:
            continue
        overlap = len(new & prev) / min(len(new), len(prev))
        if overlap >= DUPLICATE_OVERLAP:
            return True
    return False
