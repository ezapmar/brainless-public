"""output_guard.py: model output that is about the model, not about the source.

A compile call sometimes answers the harness instead of the task: "Write is
disabled in this session, so I'll output the compiled summary...", "here is
the compiled status mirror", "please approve the write". Written to disk
verbatim, that sentence becomes the page's summary_en, the index line and
what search matches. On 2026-09-22 ten wiki pages carried it, one of them for
a month. Two shapes are caught:

  chatter   the model narrating its tools, permissions or delivery, near the
            start or the end of the page, where it lands (a sentence in the
            middle that discusses a tool is left alone).
  nested    a second frontmatter block inside the body: the model wrapped its
            answer around a copy of the page (the 2026-08-24 corruption).

Deterministic and fast: the compiler refuses such output before writing, the
concept validator rejects it, and lint lists the pages already on disk.
"""
import re

HEAD_CHARS = 1500
TAIL_CHARS = 800

_CHATTER = re.compile("|".join([
    r"\bwrite(?: tool)? (?:is|isn't|is not|was) (?:disabled|not available|unavailable|available)",
    r"\bwrite(?: tool)?\s+(?:devre d[ıi][şs][ıi]|kapal[ıi])",
    r"\b(?:so|,) I(?:'ll| will) (?:output|return|print|give)",
    r"\bhere(?: is|'s) the (?:compiled|updated|full|complete|requested)\b",
    r"\bplease approve the write\b",
    r"\bready to (?:save|write|paste) (?:to|into|as)\b",
    r"\bI (?:couldn't|could not|can't|cannot) (?:save|write) (?:it|the file|this)",
    r"\bI (?:don't|do not) have (?:write |file )?(?:access|permission)",
    r"\bif you (?:re-?enable|grant|approve) (?:the )?(?:write|file|permission)",
    r"\bI output (?:only )?the (?:markdown|page|summary)\b",
]), re.IGNORECASE)

_NESTED_FM = re.compile(r"\n---\s*\n(?:[a-z_]+:.*\n){1,}?(?:summary_en|lang):", re.IGNORECASE)


def _split(text: str) -> tuple[str, str]:
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            return text[4:end], text[end + 4:]
    return "", text


_FENCE = re.compile(r"^```.*?^```", re.S | re.M)


def problems(text: str) -> list[str]:
    """Reasons this output is not a page (empty = fine). Fenced code is skipped:
    a page may legitimately show a template or a proposal with frontmatter."""
    fm, body = _split(text)
    body = _FENCE.sub("", body)
    found = []
    zones = (fm + "\n" + body[:HEAD_CHARS], body[-TAIL_CHARS:])
    for zone in zones:
        m = _CHATTER.search(zone)
        if m:
            found.append(f"model chatter: '{m.group(0)}'")
            break
    if _NESTED_FM.search(body):
        found.append("a second frontmatter block inside the body")
    return found


def is_bad(text: str) -> bool:
    return bool(problems(text))
