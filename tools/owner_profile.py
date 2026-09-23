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
EDITOR_DIR = (os.environ.get("BRAINLESS_EDITOR_DIR") or _fm.get("editor_dir") or "Writings/Editor").strip().strip("/")
WRITINGS_DIR = (os.environ.get("BRAINLESS_WRITINGS_DIR") or _fm.get("writings_dir") or "Writings").strip().strip("/")
DRAFTS_DIR = (os.environ.get("BRAINLESS_DRAFTS_DIR") or _fm.get("drafts_dir") or "Writings/Drafts").strip().strip("/")
NARRATIVES_DIR = (os.environ.get("BRAINLESS_NARRATIVES_DIR") or _fm.get("narratives_dir") or "Writings/Narratives").strip().strip("/")
PRIVATE_SEGMENTS = tuple(x.strip() for x in (os.environ.get("BRAINLESS_PRIVATE_SEGMENTS")
                                              or _fm.get("private_segments") or "").split(",") if x.strip())
GENERIC_PRIVATE_SEGMENTS = ("Official Docs", "Security Incidents")
PRIVATE_SUFFIXES = (" - Health",)


def _csv(env_key, fm_key, default=()):
    raw = os.environ.get(env_key) or _fm.get(fm_key) or ""
    return tuple(x.strip() for x in raw.split(",") if x.strip()) or default

LONGFORM_DIRS = _csv("BRAINLESS_LONGFORM_DIRS", "longform_dirs")
# Path fragments that are private wherever they appear, for folders whose exact
# name changes (an HR export named "Report-<date>-<hash>" is a new name
# every time). Matched case-insensitively as substrings by the compiler's
# privacy guard and asserted untracked by tools/tests/test_gitignore_guards.py.
PRIVATE_NAME_PARTS = _csv("BRAINLESS_PRIVATE_NAME_PARTS", "private_name_parts")
# Path fragments that mark a concept personal (family members' names, for instance).
# They live in PROFILE.md, not in code, because code is exported and names are not.
PERSONAL_MARKERS = _csv("BRAINLESS_PERSONAL_MARKERS", "personal_markers")
CORPUS_DIRS = _csv("BRAINLESS_CORPUS_DIRS", "corpus_dirs")


# Vault-relative homes that must never reach the remote. health_check.py reports on
# them and tools/tests/test_gitignore_guards.py asserts git really ignores them; both
# read this list rather than keeping a copy, because two copies drift and the drift is
# silent until something sensitive is already committed.
PROTECTED_HOMES = tuple(x.strip("/") for x in _csv(
    "BRAINLESS_PROTECTED_HOMES", "protected_homes",
    ("Personal/Official Docs", "Thinking/_local")))
# The .gitignore pattern nets that sit under the rules naming a single path, so one
# rename cannot expose a home on its own.
GITIGNORE_NETS = _csv("BRAINLESS_GITIGNORE_NETS", "gitignore_nets",
                      ("**/Official Docs/", "**/* - Health/", "**/_local/",
                       "**/Security Incidents/"))


def private_segment_patterns():
    """Regexes matching a private segment in a raw or in a compiled path.

    The compiled layer flattens and slugifies names, so a folder rule does not
    reach it: "Gülşen Çimen" arrives as "gulsen-cimen". Three shapes are generated
    per segment, because a name gets typed all three ways: as written, slugified
    with dashes, and with the Turkish letters folded to ASCII but the spaces
    kept, which is what a hand-made folder tends to look like. The guard test
    reads these so it never has to spell a private name out itself.
    """
    folds = str.maketrans("üûùúıîìíöôòóçÇğĞşŞÜÛÙÚİÎÌÍÖÔÒÓ",
                          "uuuuiiiioooocCgGsSUUUUIIIIOOOO")
    out = []
    for seg in PRIVATE_SEGMENTS:
        ascii_seg = re.sub(r"\s+", " ", seg.translate(folds).lower()).strip()
        slug = re.sub(r"[^a-z0-9]+", "-", ascii_seg).strip("-")
        shapes = {re.escape(seg), re.escape(slug), re.escape(ascii_seg)}
        out.append("(?i)" + "|".join(sorted(shapes)))
    return out


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


def lang_name(native=False, lang=None):
    """'Turkish' / 'English' (or the native form 'Türkçe' when native=True).

    lang: name a specific code instead of the vault default. Callers that work
    per item rather than per vault (a note in its own language, a dialectic
    topic) pass the code they detected.
    """
    code = (lang or LANG).lower()
    table = _LANG_NATIVE if native else _LANG_NAMES
    return table.get(code) or _LANG_NAMES.get(code) or code


def output_lang_directive(lang=None):
    """One sentence to append to any prompt that produces vault text.

    Called with no argument this is the vault-wide directive it always was.
    Pass a code to answer in the language of the thing being discussed, which is
    what weekly_research and dialectic do: a Turkish note gets a Turkish answer,
    an English one an English answer, in the same vault.
    """
    return f"Write in {lang_name(lang=lang)}. Never use em dashes or en dashes."


def possessive():
    """"<owner>'s" for English prompt bodies."""
    return f"{OWNER}'s"
