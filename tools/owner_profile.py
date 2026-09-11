#!/usr/bin/env python3
"""Owner profile for prompts: who the vault belongs to and which language the
LLM should write in. This is the seam that makes the engine someone-else-runnable
(open-source plan, Phase 1).

Resolution order, first hit wins:
  1. environment: BRAINLESS_OWNER_NAME, BRAINLESS_OUTPUT_LANG
  2. <vault>/_Agent-Context/PROFILE.md frontmatter: owner_name, output_lang
  3. defaults: "the owner", "en"

PROFILE.md is a private data file (stubbed in the public export). Language codes:
any ISO code such as "en", "tr", "de"; deterministic strings come from
tools/locale/<code>/ with English as the fallback, prompts get the language name.

Usage in a prompt body:
    from owner_profile import OWNER, LANG, lang_name, output_lang_directive
    f"... notes {OWNER} captured today ... Write in {lang_name()}."  # OWNER from PROFILE.md
"""
import os
import re

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
PROFILE_FILE = os.path.join(VAULT, "_Agent-Context", "PROFILE.md")
# Display names for common codes; any other code is passed to prompts as the code itself.
_LANG_NAMES = {"tr": "Turkish", "en": "English", "de": "German", "fr": "French", "es": "Spanish",
               "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "ja": "Japanese", "zh": "Chinese",
               "ko": "Korean", "ru": "Russian", "ar": "Arabic"}
_LANG_NATIVE = {"tr": "Türkçe", "en": "English", "de": "Deutsch", "fr": "Français", "es": "Español",
                "it": "Italiano", "pt": "Português", "nl": "Nederlands"}


def _frontmatter(path):
    try:
        with open(path, errors="replace") as fh:
            text = fh.read()
    except OSError:
        return {}
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


_fm = _frontmatter(PROFILE_FILE)
OWNER = (os.environ.get("BRAINLESS_OWNER_NAME") or _fm.get("owner_name") or "the owner").strip()
OWNER_FULL = (os.environ.get("BRAINLESS_OWNER_FULL_NAME") or _fm.get("owner_full_name") or OWNER).strip()
LANG = (os.environ.get("BRAINLESS_OUTPUT_LANG") or _fm.get("output_lang") or "en").strip().lower()
if not re.fullmatch(r"[a-z]{2,3}(-[a-z0-9]{2,8})?", LANG):
    LANG = "en"
# Company area inside Work/ that the compiler and dashboard treat specially (vault-relative).
COMPANY_AREA = (os.environ.get("BRAINLESS_COMPANY_AREA") or _fm.get("company_area") or "Work").strip().strip("/")
# Name of the always-on worker machine; its commits carry "(<name>)" as suffix.
WORKER = (os.environ.get("BRAINLESS_WORKER_NAME") or _fm.get("worker_name") or "worker").strip()
# Extra private path segments (comma separated) on top of the generic ones.
PRIVATE_SEGMENTS = tuple(x.strip() for x in (os.environ.get("BRAINLESS_PRIVATE_SEGMENTS")
                                              or _fm.get("private_segments") or "").split(",") if x.strip())
GENERIC_PRIVATE_SEGMENTS = ("Official Docs", "Security Incidents")
PRIVATE_SUFFIXES = (" - Health",)


def _section(path, heading):
    try:
        with open(path, errors="replace") as fh:
            text = fh.read()
    except OSError:
        return ""
    m = re.search(r"^## " + re.escape(heading) + r"\s*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    return m.group(1).strip() if m else ""


_DEFAULT_CROSS_LINK = ("CRITICAL CROSS-LINK RULE: personal and work effects must be surfaced. "
                       "Whenever a personal topic affects work or the reverse, link both ways.")
CROSS_LINK_RULE = _section(PROFILE_FILE, "Cross-link rule") or _DEFAULT_CROSS_LINK


def lang_name(native=False):
    """'Turkish' / 'English' (or the native form 'Türkçe' when native=True)."""
    table = _LANG_NATIVE if native else _LANG_NAMES
    return table.get(LANG) or _LANG_NAMES.get(LANG) or LANG


def output_lang_directive():
    """One sentence to append to any prompt that produces vault text."""
    return f"Write in {lang_name()}. Never use em dashes or en dashes."


def possessive():
    """"<owner>'s" for English prompt bodies."""
    return f"{OWNER}'s"
