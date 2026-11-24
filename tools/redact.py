#!/usr/bin/env python3
"""redact.py: the personal-data and secret patterns, and the cloud redactor.

One definition, two users: tools/privacy_scan.py counts and gates with these
patterns, and tools/llm.py masks every prompt bound for a cloud provider with
redact_for_cloud(). Local providers see the text as it is. Keep this module
free of imports beyond the standard library: llm.py loads it on every call.

redact_for_cloud gives numbered placeholders, stable inside one prompt, so a
model can still tell two people's addresses apart ("[e-posta 1]", "[e-posta 2]")
without seeing either. Nothing maps them back: the mapping never leaves the call.
"""
from __future__ import annotations

import re

# Role mailboxes are not personal data; a person's address is.
_ROLE_LOCAL = {"info", "support", "destek", "hello", "merhaba", "noreply", "no-reply", "privacy",
               "dpo", "kvkk", "sales", "satis", "iletisim", "contact", "press", "basin", "careers",
               "kariyer", "hr", "ik", "billing", "fatura", "muhasebe", "admin", "team", "help",
               "security", "legal", "hukuk", "office", "ofis", "notifications", "bildirim"}
_SAFE_DOMAINS = ("example.", "github.com", "report.spiky.ai", "users.noreply.github.com")
_FILE_TLDS = {"png", "jpg", "jpeg", "gif", "svg", "webp", "pdf", "md", "js", "css"}

EMAIL = re.compile(r"(?<![\w.+-])([\w.+-]+)@([\w-]+(?:\.[\w-]+)+)")
PHONE = re.compile(
    r"(?<![\w+])(?:"
    r"\+90[ .-]?\(?5\d{2}\)?[ .-]?\d{3}[ .-]?\d{2}[ .-]?\d{2}"   # +90 5xx xxx xx xx
    r"|0\(?5\d{2}\)?[ .-]?\d{3}[ .-]?\d{2}[ .-]?\d{2}"            # 05xx xxx xx xx
    r"|\+90[ .-]?\(?[2-4]\d{2}\)?[ .-]?\d{3}[ .-]?\d{2}[ .-]?\d{2}"  # +90 landline
    r"|\+44[ .-]?\(?0?\)?7\d{3}[ .-]?\d{3}[ .-]?\d{3}"            # +44 7xxx xxx xxx
    r"|\+44[ .-]?\(?0?\)?[1-3]\d{2,3}[ .-]?\d{3}[ .-]?\d{3,4}"    # +44 landline
    r"|07\d{3}[ .-]?\d{6}"                                        # 07xxx xxxxxx
    r")(?![\w])")
IBAN = re.compile(r"(?<![A-Z0-9])(TR\d{2}(?: ?\d{4}){5} ?\d{2}|GB\d{2} ?[A-Z]{4}(?: ?\d{4}){3} ?\d{2})(?![0-9])")
TCKN = re.compile(r"(?<!\d)([1-9]\d{10})(?!\d)")
# Checksum-valid numbers that are public test values, used in DLP rules and docs.
_TEST_TCKN = {"12345678950", "11111111110", "10000000146"}

SECRETS = [
    ("private-key", re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY")),
    ("bot-token", re.compile(r"(?<!\d)\d{8,10}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])")),
    ("github-token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{40,}")),
    ("google-api-key", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    ("anthropic-key", re.compile(r"(?<![A-Za-z])sk-ant-[a-z]+\d*-[A-Za-z0-9_-]{20,}")),
    ("openai-key", re.compile(r"(?<![A-Za-z])sk-(?:proj-)?[A-Za-z0-9_-]{32,}")),
    ("google-oauth-secret", re.compile(r"GOCSPX-[A-Za-z0-9_-]{20,}")),
    ("slack-token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("aws-access-key", re.compile(r"(?<![A-Z0-9])AKIA[0-9A-Z]{16}(?![0-9A-Z])")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{6,}")),
]

REDACT = {"email": "[e-posta]", "phone": "[telefon]", "iban": "[IBAN]", "tckn": "[TCKN]"}


def tckn_valid(s: str) -> bool:
    """The TC kimlik checksum. Cuts the false positives a bare 11-digit match
    gives on invoice and order numbers."""
    if len(s) != 11 or not s.isdigit() or s[0] == "0":
        return False
    d = [int(c) for c in s]
    if ((sum(d[0:9:2]) * 7 - sum(d[1:8:2])) % 10) != d[9]:
        return False
    return sum(d[:10]) % 10 == d[10]


def _email_ok(local: str, domain: str) -> bool:
    if local.lower() in _ROLE_LOCAL:
        return True
    dom = domain.lower()
    if dom.rsplit(".", 1)[-1] in _FILE_TLDS:
        return True  # icon@2x.png
    return any(dom.startswith(s) or dom == s or dom.endswith("." + s) for s in _SAFE_DOMAINS)


def find_personal(text: str) -> list[tuple[str, str, int]]:
    """(kind, match, start) for every personal-data finding in text."""
    out = []
    for m in EMAIL.finditer(text):
        if not _email_ok(m.group(1), m.group(2)):
            out.append(("email", m.group(0), m.start()))
    for m in PHONE.finditer(text):
        out.append(("phone", m.group(0), m.start()))
    for m in IBAN.finditer(text):
        out.append(("iban", m.group(0), m.start()))
    for m in TCKN.finditer(text):
        if m.group(1) not in _TEST_TCKN and tckn_valid(m.group(1)):
            out.append(("tckn", m.group(1), m.start()))
    return out


def find_secrets(text: str) -> list[tuple[str, str, int]]:
    return [(kind, m.group(0), m.start()) for kind, rx in SECRETS for m in rx.finditer(text)]


def redact(text: str) -> tuple[str, int]:
    """Text with personal findings replaced, and how many were replaced."""
    hits = sorted(find_personal(text), key=lambda h: h[2], reverse=True)
    for kind, value, start in hits:
        text = text[:start] + REDACT[kind] + text[start + len(value):]
    return text, len(hits)


_LABEL = {"email": "e-posta", "phone": "telefon", "iban": "IBAN", "tckn": "TCKN"}


def redact_for_cloud(text: str) -> tuple[str, dict[str, int]]:
    """Text with personal data and secrets replaced by numbered placeholders,
    and {kind: count}. The same value gets the same placeholder throughout."""
    hits = find_personal(text) + [(k, v, s) for k, v, s in find_secrets(text)]
    if not hits:
        return text, {}
    # Overlaps (a phone inside a token): keep the earliest, longest match.
    hits.sort(key=lambda h: (h[2], -len(h[1])))
    kept, end = [], -1
    for h in hits:
        if h[2] >= end:
            kept.append(h)
            end = h[2] + len(h[1])
    numbers: dict[tuple[str, str], int] = {}
    per_kind: dict[str, int] = {}
    counts: dict[str, int] = {}
    for kind, value, _start in kept:
        key = (kind, value)
        if key not in numbers:
            per_kind[kind] = per_kind.get(kind, 0) + 1
            numbers[key] = per_kind[kind]
        counts[kind] = counts.get(kind, 0) + 1
    out = text
    for kind, value, start in sorted(kept, key=lambda h: h[2], reverse=True):
        label = _LABEL.get(kind, "gizli")
        out = out[:start] + f"[{label} {numbers[(kind, value)]}]" + out[start + len(value):]
    return out, counts
