#!/usr/bin/env python3
"""Locale strings for deterministic, user-facing text (labels, headings, bot
replies). LLM prompts do not use this: they are written in English and carry an
output-language directive from owner_profile instead.

Strings live in tools/locale/<lang>/<namespace>.json, one flat object per
namespace (a namespace is usually the script name). The language comes from
owner_profile.LANG (PROFILE.md output_lang or BRAINLESS_OUTPUT_LANG). English is
the fallback for any key a language does not define, so a new language is a new
directory with only the keys someone bothered to translate.

    from i18n import t, t_list
    t("health_check.last_commit")            -> "Last commit"
    t("crm_capture.counts", orgs=3, deals=2) -> formatted with str.format
    t_list("telegram_capture.cancel_words")  -> list: current language + English merged
    t("...", lang="tr")                      -> explicit language for one call

A missing key returns the key itself, so a typo is visible in the output
instead of raising in a scheduled job.
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from owner_profile import LANG  # noqa: E402

LOCALE_DIR = os.path.join(_HERE, "locale")
_cache = {}


def _load(lang):
    if lang in _cache:
        return _cache[lang]
    table = {}
    d = os.path.join(LOCALE_DIR, lang)
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".json"):
                continue
            ns = fn[:-5]
            try:
                with open(os.path.join(d, fn), encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError) as e:
                print(f"[i18n] cannot read {d}/{fn}: {e}", file=sys.stderr)
                continue
            for k, v in data.items():
                table[f"{ns}.{k}"] = v
    _cache[lang] = table
    return table


def languages():
    """Language codes that have a locale directory."""
    try:
        return sorted(x for x in os.listdir(LOCALE_DIR) if os.path.isdir(os.path.join(LOCALE_DIR, x)))
    except OSError:
        return ["en"]


def raw(key, lang=None):
    """The untouched value (string or list) for key, with English fallback."""
    lang = lang or LANG
    val = _load(lang).get(key)
    if val is None and lang != "en":
        val = _load("en").get(key)
    return val


def t(key, lang=None, **kw):
    """Translated string, formatted with kw when given."""
    val = raw(key, lang)
    if not isinstance(val, str):
        return key
    if kw:
        try:
            return val.format(**kw)
        except (KeyError, IndexError, ValueError):
            return val
    return val


def t_list(key, lang=None):
    """List value merged across the current language and English, in that order."""
    out = []
    for lg in dict.fromkeys([lang or LANG, "en"]):
        val = _load(lg).get(key)
        if isinstance(val, list):
            out.extend(x for x in val if x not in out)
        elif isinstance(val, str) and val not in out:
            out.append(val)
    return out


if __name__ == "__main__":
    # Quick check: list keys missing from each non-English locale.
    en = _load("en")
    for lg in languages():
        if lg == "en":
            continue
        missing = sorted(k for k in en if k not in _load(lg))
        print(f"{lg}: {len(_load(lg))} keys, {len(missing)} missing from en" + (": " + ", ".join(missing[:20]) if missing else ""))
    print(f"en: {len(en)} keys")
