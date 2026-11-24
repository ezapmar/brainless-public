#!/usr/bin/env python3
"""cloud_gate.py: which vault sources may never reach a cloud provider (P0.4).

One rule, two enforcers. compile_resources.py reads the rule to pick a local
lane before it builds a prompt; llm.py reads the same rule to refuse the call if
a blocked path is handed to a cloud provider anyway. Keeping the rule here, not
in either caller, is the point: two copies drift and the drift is silent until
something sensitive is already sent.

Two inputs decide, and either one is enough:

  cloud_exclude   a vault-relative folder listed in _Agent-Context/PROFILE.md
                  (or BRAINLESS_CLOUD_EXCLUDE). Everything under it is local
                  only. Naming a folder is how a whole area leaves the cloud
                  without touching a single note.
  privacy:        a note's own frontmatter, one of the four levels below. This
                  is the human field: an owner marks the one note that matters,
                  and the folder rule covers the rest.

Levels. `privacy:` accepts public, internal, personal, secret.

  public    may go anywhere. Nothing in the vault is published, so this only
            means "no personal data at all".
  internal  may reach a cloud model; tools/redact.py still masks e-mail, phone,
            IBAN, TC Kimlik and secrets on the way out. The default.
  personal  local only. The compiler still summarises it, on the local model
            (P0.4 decision b); a cloud call is refused.
  secret    never compiled at all. Not summarised, not indexed into .wiki.

A value that is none of the four is treated as `personal` and logged, never as
"no restriction": the field is a security control, and a typo must not unlock a
note. The first word decides, so `internal (no customer names)` is internal.

Why not fold cloud_exclude into the compiler's PRIVATE_SEGMENTS list: that list
means "never compiled", and cloud-excluded folders ARE compiled, locally. Folding
them together would silently produce decision (c), no summary at all, instead of
the one that was taken.

Notes are read from the head of the file (4 KB), which is where frontmatter
lives; a block longer than that is not frontmatter in practice. Results are
cached by (path, mtime, size) because the compiler walks thousands of files.
"""
from __future__ import annotations

import os
import stat
import unicodedata
from pathlib import Path

from owner_profile import CLOUD_EXCLUDE
from paths import vault_root

VAULT = Path(vault_root())

PRIVACY_LEVELS = ("public", "internal", "personal", "secret")
# Levels a cloud provider may not see. `internal` is absent on purpose: the
# redaction gate is what makes it safe to send.
LOCAL_ONLY = ("personal",)
NEVER_COMPILED = ("secret",)
_HEAD_BYTES = 4096
_cache: dict[str, tuple[float, int, str]] = {}


def norm(rel) -> str:
    """A vault-relative path in one shape: NFC, forward slashes, no leading slash.

    macOS hands file names back in NFD while PROFILE.md and the code are NFC;
    without this, an accented folder name slips past the rule (the 2026-08-28
    audit found 88 files on GitHub for exactly that reason).
    """
    text = unicodedata.normalize("NFC", str(rel).replace("\\", "/"))
    text = text.removeprefix("./").lstrip("/")
    return text


def rel_of(path) -> str:
    """The vault-relative form of a path, absolute or already relative."""
    text = norm(path)
    root = norm(VAULT)
    if root and (text == root or text.startswith(root + "/")):
        text = text[len(root):].lstrip("/")
    return text


def _abs(rel) -> Path:
    p = Path(str(rel))
    return p if p.is_absolute() else VAULT / rel_of(rel)


def matching_root(rel, roots=None) -> str | None:
    """The cloud_exclude entry this path sits under, or None."""
    text = rel_of(rel).casefold()
    for root in (CLOUD_EXCLUDE if roots is None else roots):
        r = norm(root).casefold().strip("/")
        if r and (text == r or text.startswith(r + "/")):
            return norm(root).strip("/")
    return None


def cloud_excluded(rel, roots=None) -> bool:
    return matching_root(rel, roots) is not None


def privacy(rel) -> str:
    """The note's raw `privacy:` value, lower-cased; "" when absent or unreadable."""
    path = _abs(rel)
    try:
        st = os.stat(path)
    except OSError:
        return ""
    if not stat.S_ISREG(st.st_mode):
        return ""
    key = norm(path)
    hit = _cache.get(key)
    if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
        return hit[2]
    level = ""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            head = fh.read(_HEAD_BYTES)
    except OSError:
        return ""
    if head.startswith("---"):
        from frontmatter import parse
        level = (parse(head).get("privacy") or "").strip().lower()
    _cache[key] = (st.st_mtime, st.st_size, level)
    return level


def level_of(raw: str) -> str:
    """The level a `privacy:` value means, or "" when it is not one of the four.

    The first word decides, so `internal (no customer names)` is internal and a
    hand-written sentence is not. An unrecognised value is NOT read as "no
    restriction": this field is a security control, and a typo must not quietly
    unlock a note. `block_reason` treats it as personal and logs it.
    """
    word = (raw or "").strip().lower().split()[0].rstrip(".,;:") if (raw or "").strip() else ""
    return word if word in PRIVACY_LEVELS else ""


def block_reason(rel, roots=None) -> str | None:
    """Why this source may not go to a cloud provider, or None.

    Returns "cloud_exclude", "privacy:personal", "privacy:secret" or
    "privacy:unrecognised". The order matters only for the log line: a folder
    rule is the cheaper answer.
    """
    if cloud_excluded(rel, roots):
        return "cloud_exclude"
    raw = privacy(rel)
    if not raw:
        return None
    level = level_of(raw)
    if level in (*LOCAL_ONLY, *NEVER_COMPILED):
        return f"privacy:{level}"
    if not level:
        return "privacy:unrecognised"
    return None


def is_secret(rel, roots=None) -> bool:
    """True when the note must not be compiled at all."""
    return level_of(privacy(rel)) in NEVER_COMPILED


def source_hash(rel) -> str:
    """Stable short hash of a vault-relative path.

    Not used by the egress ledger, which records no source identity at all. It
    is here for a caller that needs to compare two paths without storing either
    (the ledger used it until 2026-11-21 and a reader of old rows may still
    want it).
    """
    import hashlib
    return hashlib.sha256(rel_of(rel).encode("utf-8")).hexdigest()[:16]
