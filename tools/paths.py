#!/usr/bin/env python3
"""Where the vault is and what its human folders are called.

Vault root, first hit wins:
  1. BRAINLESS_VAULT
  2. the checkout this file lives in (tools/ sits at the vault root)

The second rule replaces the old "~/projects/brainless" fallback. It was right on
one machine and wrong on every other one, while the directory holding tools/ is
the vault on every install, because the engine is cloned into the vault.

Folders. The engine speaks in logical keys, never in folder names, so a vault can
call its inbox "Gelen" and its daily notes "Notlar" (the lite profile names them
in the owner's language; see tools/locale/<lang>/folders.json). A key resolves:
  1. BRAINLESS_FOLDER_<KEY> (config.py fills it from brainless.toml [folders])
  2. the default below, which is the full vault's layout

beliefs, decisions and ideas default to subfolders of whatever "thinking" is, and
daily defaults to "<thinking>/Daily", so renaming the thinking home moves them too
unless they are set on their own. Values are vault-relative with "/" separators.
.wiki/, _Agent-Context/ and .agents/state/ are engine internals and stay fixed.
"""
import json
import os
import unicodedata
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))

FOLDER_KEYS = ("inbox", "library", "thinking", "daily", "beliefs", "decisions", "ideas")
_DEFAULTS = {"inbox": "Inbox", "library": "Library", "thinking": "Thinking"}
_UNDER_THINKING = {"daily": "Daily", "beliefs": "Beliefs", "decisions": "Decisions", "ideas": "Ideas"}


def vault_root():
    """Absolute vault path as a string. Read at call time, so tests can patch env."""
    env = os.environ.get("BRAINLESS_VAULT")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return os.path.dirname(_HERE)


def _clean(rel):
    raw = unicodedata.normalize("NFC", rel.strip().replace("\\", "/"))
    clean = raw.strip("/")
    if (not clean or raw.startswith("/") or os.path.isabs(raw) or ":" in clean
            or ".." in clean.split("/")):
        raise ValueError(f"folder must be a vault-relative path: {rel!r}")
    return clean


def folder(key):
    """Vault-relative folder for a logical key, e.g. folder("daily") -> "Thinking/Daily"."""
    if key not in FOLDER_KEYS:
        raise KeyError(key)
    env = os.environ.get(f"BRAINLESS_FOLDER_{key.upper()}")
    if env and env.strip():
        return _clean(env)
    if key in _UNDER_THINKING:
        return f"{folder('thinking')}/{_UNDER_THINKING[key]}"
    return _DEFAULTS[key]


def folder_path(key, vault=None):
    """Absolute Path for a logical folder key."""
    return Path(vault or vault_root()) / folder(key)


def extra_sources():
    """Additional vault-relative summary sources (BRAINLESS_SOURCES, comma separated)."""
    raw = os.environ.get("BRAINLESS_SOURCES") or ""
    return tuple(_clean(x) for x in raw.split(",") if x.strip())


def skeleton(lang="en"):
    """Folder names for a new lite vault in the owner's language, English as fallback.

    Returns {key: vault-relative name}. The installer writes these into
    brainless.toml, so once a vault exists its names never depend on the language.
    """
    names = {}
    for code in ("en", lang):
        try:
            with open(os.path.join(_HERE, "locale", code, "folders.json"), encoding="utf-8") as fh:
                names.update(json.load(fh))
        except (OSError, ValueError):
            continue
    return {k: _clean(names[k]) for k in FOLDER_KEYS if k in names}
