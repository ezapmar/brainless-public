#!/usr/bin/env python3
"""claude_guard.py: Claude Code hooks for the vault.

The vault's standing rules used to live only in prompts: no em or en dash, ask
before writing a human area, `lang` and `summary_en` on every wiki page,
briefings in one place, no secrets in files, read the context files first. A
prompt is a preference. An agent that can do the wrong thing eventually will,
in a context nobody predicted, so these checks run on every tool call instead.

Usage (wired in .claude/settings.json; reads the hook JSON on stdin):
  claude_guard.py pre       PreToolUse: dash, human area, secret, briefing, deletion
  claude_guard.py post      PostToolUse: .wiki frontmatter
  claude_guard.py session   SessionStart: inject CONTEXT.md, PROJECTS-ACTIVE.md, LEARNINGS.md

Three rules keep the hooks worth having:
- Silent on pass. A hook that talks when nothing is wrong trains everyone to
  ignore it on the day it matters.
- Fail open. A bug here (bad JSON, a missing file) exits 0 with a note on
  stderr. Only a check can block, never the checker, so a guard bug cannot
  quietly stop a scheduled run.
- No judgement. Style, privacy tiers and loopback filing need judgement and
  stay in the prompts and in Writings/Editor/editor_lint.py.
"""
import json
import os
import re
import sys
from pathlib import Path

VAULT = Path(os.environ.get("BRAINLESS_VAULT") or Path(__file__).resolve().parents[2])
sys.path.insert(0, str(VAULT / "tools"))

DASHES = (chr(0x2014), chr(0x2013))
HUMAN_AREAS = ("Work/", "Personal/", "Library/", "Thinking/", "Inbox/", "raw/",
               "Writings/", "Galatasaray/")
# Labels from export_public.LEAK_PATTERNS that mean a real secret. The others
# (11 digits, 64 hex, Tailscale addresses) match phone numbers, event ids and
# docs far too often to block a write on.
SECRET_LABELS = ("private key block", "bot token", "github token", "google api key",
                 "openai key", "slack token", "aws access key", "jwt", "iban (TR)")
BRIEFING_NAME = re.compile(r"(?i)(briefing|brifing)[ _-]?\d{4}-\d{2}-\d{2}")
BRIEFING_HOME = re.compile(r"^Daily Briefings/daily-briefing-\d{4}-\d{2}-\d{2}\.md$")
TMP_ROOTS = ("/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/")
DESTRUCTIVE_GIT = re.compile(
    r"\bgit\s+(?:-C\s+\S+\s+)?(?:rm\b|clean\b|reset\s+--hard|push\s+.*(?:-f\b|--force)|"
    r"checkout\s+(?:--\s+)?\.(?:\s|$)|restore\s+\.)")
RM_CMD = re.compile(r"(?:^|[;&|(]\s*|\bxargs\s+|\bsudo\s+)rm\s+([^;&|)]*)")
FIND_DELETE = re.compile(r"\bfind\b[^;&|]*\s-delete\b")


# ─── output ──────────────────────────────────────────────────────
def decide(event: str, decision: str, reason: str):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": event, "permissionDecision": decision,
        "permissionDecisionReason": reason}}, ensure_ascii=False))


def rel(path: str) -> str:
    """Vault-relative path, or '' for a path outside the vault."""
    if not path:
        return ""
    p = Path(path)
    if not p.is_absolute():
        p = VAULT / p
    try:
        return p.resolve().relative_to(VAULT.resolve()).as_posix()
    except (ValueError, OSError):
        return ""


# ─── what a tool call writes ─────────────────────────────────────
def writes(tool: str, ti: dict) -> tuple[str, str, str]:
    """(path, new text, replaced text) for a write tool; replaced text is what
    the new text overwrites, so a legacy dash already in a file is not blamed
    on the edit that touches it."""
    if tool == "Write":
        path = ti.get("file_path", "")
        old = ""
        try:
            old = Path(path).read_text(errors="replace")
        except OSError:
            pass
        return path, ti.get("content", ""), old
    if tool == "Edit":
        return ti.get("file_path", ""), ti.get("new_string", ""), ti.get("old_string", "")
    if tool == "MultiEdit":
        edits = ti.get("edits") or []
        return (ti.get("file_path", ""),
                "\n".join(e.get("new_string", "") for e in edits),
                "\n".join(e.get("old_string", "") for e in edits))
    if tool == "NotebookEdit":
        return ti.get("notebook_path", ""), ti.get("new_source", ""), ""
    return "", "", ""


def dash_count(text: str) -> int:
    return sum(text.count(d) for d in DASHES)


def dash_line(text: str) -> str:
    for line in text.splitlines():
        if any(d in line for d in DASHES):
            return line.strip()[:80]
    return ""


def secret_in(text: str) -> str:
    from export_public import LEAK_PATTERNS
    for label, rx in LEAK_PATTERNS:
        if label in SECRET_LABELS and rx.search(text):
            return label
    return ""


def drafts_dirs() -> tuple[str, ...]:
    try:
        from owner_profile import DRAFTS_DIR, NARRATIVES_DIR
        return tuple(d.rstrip("/") + "/" for d in (DRAFTS_DIR, NARRATIVES_DIR))
    except Exception:
        return ("Writings/Drafts/", "Writings/Narratives/")


# ─── checks ──────────────────────────────────────────────────────
def check_write(tool: str, ti: dict):
    """(decision, reason) for a write tool, or None."""
    path, new, old = writes(tool, ti)
    if any(d in os.path.basename(path) for d in DASHES):
        return "deny", "Em and en dashes are banned in file names. Use a hyphen or a space."
    if dash_count(new) > dash_count(old):
        return "deny", (f"Em and en dashes are banned in all output. Rewrite with a comma, "
                        f"colon or full stop: {dash_line(new)!r}")
    label = secret_in(new)
    if label:
        return "deny", (f"This write contains what looks like a {label}. Secrets never go "
                        f"into files; cite where the secret lives instead.")
    r = rel(path)
    if tool == "Write" and BRIEFING_NAME.search(os.path.basename(path)) and not BRIEFING_HOME.match(r):
        return "deny", ("Briefings live only at Daily Briefings/daily-briefing-YYYY-MM-DD.md "
                        "(AGENT-RULES, Briefing Convention).")
    if r.startswith(HUMAN_AREAS) and not r.startswith(drafts_dirs()):
        return "ask", (f"{r} is in a human area. The owner approves every write there "
                       f"(CLAUDE.md, Sahiplik).")
    return None


def rm_targets_are_temporary(args: str) -> bool:
    targets = [a.strip("'\"") for a in args.split() if not a.startswith("-")]
    return bool(targets) and all(t.startswith(TMP_ROOTS) or t.startswith("$TMPDIR")
                                 for t in targets)


def check_bash(ti: dict):
    cmd = ti.get("command", "")
    if any(d in cmd for d in DASHES):
        return "deny", (f"Em and en dashes are banned in all output, commit messages and "
                        f"heredocs included: {dash_line(cmd)!r}")
    label = secret_in(cmd)
    if label:
        return "deny", f"This command contains what looks like a {label}. Read it from its file instead."
    if DESTRUCTIVE_GIT.search(cmd) or FIND_DELETE.search(cmd):
        return "ask", "This command deletes or rewrites history. Deletions need the owner's yes."
    for m in RM_CMD.finditer(cmd):
        if not rm_targets_are_temporary(m.group(1)):
            return "ask", "This command deletes files outside the temp folders. Deletions need the owner's yes."
    return None


def pre(payload: dict):
    tool = payload.get("tool_name", "")
    ti = payload.get("tool_input") or {}
    result = check_bash(ti) if tool == "Bash" else check_write(tool, ti)
    if result:
        decide("PreToolUse", *result)


def post(payload: dict):
    tool = payload.get("tool_name", "")
    if tool not in ("Write", "Edit", "MultiEdit"):
        return
    path = (payload.get("tool_input") or {}).get("file_path", "")
    r = rel(path)
    if not (r.startswith(".wiki/") and r.endswith(".md")):
        return
    parts = r.split("/")
    if any(p.startswith("_") for p in parts[1:]):     # _commands, _archive, _lint-report.md
        return
    from lint_wiki import parse_fm
    try:
        fm = parse_fm(Path(path).read_text(errors="replace"))
    except OSError:
        return
    missing = [k for k in ("lang", "summary_en") if not fm.get(k)]
    if missing:
        print(json.dumps({"decision": "block", "reason": (
            f"{r} is missing {', '.join(missing)} in its frontmatter. Every .wiki page carries "
            f"lang: and summary_en: (CLAUDE.md, Sorgu akisi 3). Add them now.")}, ensure_ascii=False))


def session(payload: dict):
    blocks = []
    for name in ("CONTEXT.md", "PROJECTS-ACTIVE.md", "LEARNINGS.md"):
        p = VAULT / "_Agent-Context" / name
        try:
            blocks.append(f"<file path=\"_Agent-Context/{name}\">\n{p.read_text()}\n</file>")
        except OSError:
            continue
    if blocks:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": ("Session-start context required by CLAUDE.md; already loaded, "
                                  "no need to read these files again this session.\n\n"
                                  + "\n\n".join(blocks))}}, ensure_ascii=False))


def main() -> int:
    handlers = {"pre": pre, "post": post, "session": session}
    if len(sys.argv) != 2 or sys.argv[1] not in handlers:
        print(f"usage: claude_guard.py {{{'|'.join(handlers)}}}", file=sys.stderr)
        return 0
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        handlers[sys.argv[1]](payload if isinstance(payload, dict) else {})
    except Exception as exc:    # fail open: the checker must never be what blocks
        print(f"claude_guard: {type(exc).__name__}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
