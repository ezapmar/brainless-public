#!/usr/bin/env python3
"""The frontmatter block: the lines between an opening and a closing `---`.

One implementation for every tool. Until 2026-10-08 the block was parsed in
sixteen places with four different regexes: some demanded `---\\n` exactly,
some allowed trailing blanks, some stripped `#` comments, some stripped
quotes, one needed a newline after the closing fence. A page that one tool
read as "has frontmatter" another read as "no frontmatter".

    split(text)  -> (header, body)   header without fences; ("", text) if none
    strip(text)  -> body
    has(text)    -> bool
    parse(text)  -> {key: value}     flat `key: value` lines, quotes removed;
                                     comments=True also drops ` # ...` tails
    set_key(text, key, value)        replace or add one line, keep the rest
    render(fields)                   a block from a dict

Values are strings. This is not a YAML parser on purpose: the engine writes
flat frontmatter, and the one tool that needs real YAML (file_query) has it.
"""
import json
import re

FM_RE = re.compile(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", re.S)
_KEY_RE = re.compile(r"^([A-Za-z_][\w-]*)\s*:(.*)$")


def split(text: str) -> tuple[str, str]:
    """(header, body). The header is the text between the fences, without
    them; the body starts after the closing fence line."""
    m = FM_RE.match(text or "")
    if not m:
        return "", text or ""
    return m.group(1), text[m.end():]


def strip(text: str) -> str:
    """The text without its frontmatter block."""
    return split(text)[1]


def has(text: str) -> bool:
    return FM_RE.match(text or "") is not None


def _value(raw: str, comments: bool) -> str:
    v = raw.strip()
    if comments:
        v = v.partition(" #")[0].strip() if not v.startswith("#") else ""
    if len(v) >= 2 and v[0] == v[-1] == '"':
        try:
            return str(json.loads(v))
        except ValueError:
            return v[1:-1]
    if len(v) >= 2 and v[0] == v[-1] == "'":
        return v[1:-1]
    return v


def parse(text: str, *, comments: bool = False) -> dict:
    """Flat `key: value` pairs from the frontmatter. Lines that are not such a
    pair (list items, blank lines) are skipped; a repeated key keeps the last."""
    header, _ = split(text)
    out = {}
    for line in header.splitlines():
        m = _KEY_RE.match(line)
        if m:
            out[m.group(1)] = _value(m.group(2), comments)
    return out


def set_key(text: str, key: str, value) -> str:
    """Replace the key's line, or add it at the end of the block; a page with
    no block gets one. Other lines are kept as they are."""
    header, body = split(text)
    if not has(text):
        return f"---\n{key}: {value}\n---\n" + (text or "")
    lines = header.splitlines()
    pat = re.compile(rf"^{re.escape(key)}\s*:")
    for i, line in enumerate(lines):
        if pat.match(line):
            lines[i] = f"{key}: {value}"
            break
    else:
        lines.append(f"{key}: {value}")
    return "---\n" + "\n".join(lines) + "\n---\n" + body


def render(fields: dict) -> str:
    return "---\n" + "".join(f"{k}: {v}\n" for k, v in fields.items()) + "---\n"
