#!/usr/bin/env python3
"""chat_import.py: years of chat history into the vault, filtered on the way in.

A chat log is the most underrated source there is: problems worked through,
decisions reasoned about, explanations shaped to what was not understood at
the time. It is also the most sensitive, and roughly nine in ten conversations
are throwaway questions. So nothing is ingested wholesale:

  triage    list every conversation (date, words, title, first question);
            writes nothing.
  import    one markdown file per conversation into raw/chats/<source>/, the
            archive tier: searchable, not compiled. Short and personal ones are
            skipped, secrets are redacted, re-running is a no-op.
  promote   move the few worth compiling to Library/Chats/, which the compiler
            reads with a chat-specific instruction (what was being worked out,
            what was concluded, where the position changed, dated).

Redaction after ingest does not work: by then the text has spread into
summaries and links. The decision is made here, before anything lands.

Usage:
  python3 tools/chat_import.py triage  conversations.json [--min-words 150]
  python3 tools/chat_import.py import  conversations.json [--min-words 150] [--include-personal]
  python3 tools/chat_import.py promote raw/chats/claude/2025-03-01-pricing.md [...]

Reads the Claude export (chat_messages) and the ChatGPT export (mapping tree).
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
VAULT = Path(__file__).resolve().parents[1]
import os  # noqa: E402
VAULT = Path(os.environ.get("BRAINLESS_VAULT") or VAULT)
OUT_ROOT = VAULT / "raw" / "chats"
PROMOTED = VAULT / "Library" / "Chats"
SECRET_LABELS = ("private key block", "bot token", "github token", "google api key",
                 "openai key", "slack token", "aws access key", "jwt", "iban (TR)")


# ─── parsing ─────────────────────────────────────────────────────
def _when(v) -> str:
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v, tz=timezone.utc).strftime("%Y-%m-%d")
    if isinstance(v, str) and re.match(r"\d{4}-\d{2}-\d{2}", v):
        return v[:10]
    return "0000-00-00"


def _claude_text(m: dict) -> str:
    if m.get("text"):
        return m["text"]
    return "\n".join(c.get("text", "") for c in m.get("content") or [] if c.get("type") == "text")


def _chatgpt_turns(conv: dict) -> list[tuple[str, str]]:
    """Walk the mapping tree from current_node back to the root: the branch the
    user actually kept, not every regenerated alternative."""
    mapping = conv.get("mapping") or {}
    node, chain = conv.get("current_node"), []
    while node and node in mapping:
        chain.append(mapping[node])
        node = mapping[node].get("parent")
    turns = []
    for n in reversed(chain):
        msg = n.get("message") or {}
        role = (msg.get("author") or {}).get("role")
        parts = (msg.get("content") or {}).get("parts") or []
        text = "\n".join(p for p in parts if isinstance(p, str)).strip()
        if role in ("user", "assistant") and text:
            turns.append((role, text))
    return turns


def parse(data) -> list[dict]:
    """Either export -> [{id, source, title, date, turns: [(role, text)]}]."""
    convs = data if isinstance(data, list) else data.get("conversations", [])
    out = []
    for c in convs:
        if not isinstance(c, dict):
            continue
        if "chat_messages" in c:
            turns = [("user" if m.get("sender") == "human" else "assistant", _claude_text(m).strip())
                     for m in c.get("chat_messages") or []]
            out.append({"id": c.get("uuid", ""), "source": "claude", "title": c.get("name") or "untitled",
                        "date": _when(c.get("created_at")), "turns": [t for t in turns if t[1]]})
        elif "mapping" in c:
            out.append({"id": c.get("conversation_id") or c.get("id", ""), "source": "chatgpt",
                        "title": c.get("title") or "untitled", "date": _when(c.get("create_time")),
                        "turns": _chatgpt_turns(c)})
    return out


# ─── filters ─────────────────────────────────────────────────────
def words(conv) -> int:
    return sum(len(t.split()) for _, t in conv["turns"])


def first_question(conv) -> str:
    return next((t for r, t in conv["turns"] if r == "user"), "").strip().splitlines()[0][:100] \
        if conv["turns"] else ""


def personal_markers() -> tuple[str, ...]:
    try:
        from concepts import PERSONAL_MARKERS
        from i18n import t_list
        extra = tuple(m.casefold() for m in t_list("compile_resources.private_name_parts"))
        return tuple(PERSONAL_MARKERS) + extra
    except Exception:
        return ("health", "sağlık", "medical")


def is_personal(conv, markers) -> bool:
    """Title and first question only: the whole transcript mentions everything eventually."""
    head = unicodedata.normalize("NFC", f"{conv['title']} {first_question(conv)}").casefold()
    return any(m.rstrip("/") in head for m in markers)


def redact(text: str) -> tuple[str, int]:
    try:
        from export_public import LEAK_PATTERNS
    except Exception:
        return text, 0
    n = 0
    for label, rx in LEAK_PATTERNS:
        if label in SECRET_LABELS:
            text, k = rx.subn(f"[redacted {label}]", text)
            n += k
    return text, n


def slug(s: str) -> str:
    s = unicodedata.normalize("NFC", s).casefold()
    for a, b in (("ı", "i"), ("ş", "s"), ("ğ", "g"), ("ü", "u"), ("ö", "o"), ("ç", "c")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:60] or "untitled"


def no_dashes(s: str) -> str:
    return s.replace(chr(0x2014), ",").replace(chr(0x2013), "-")


# ─── output ──────────────────────────────────────────────────────
def render(conv) -> str:
    body, secrets = [], 0
    for role, text in conv["turns"]:
        text, n = redact(text)
        secrets += n
        body.append(f"### {'User' if role == 'user' else 'Assistant'}\n\n{no_dashes(text)}\n")
    title = no_dashes(conv["title"]).replace('"', "'")
    fm = [f'title: "{title}"', f"date: {conv['date']}", f"source: {conv['source']}",
          f"conversation_id: {conv['id']}", f"turns: {len(conv['turns'])}", f"words: {words(conv)}",
          f"imported: {datetime.now().strftime('%Y-%m-%d')}"]
    if secrets:
        fm.append(f"redacted: {secrets}")
    return "---\n" + "\n".join(fm) + "\n---\n# " + title + "\n\n" + "\n".join(body)


def existing_ids(root: Path) -> set[str]:
    ids = set()
    for p in list(root.rglob("*.md")) + list(PROMOTED.rglob("*.md")):
        try:
            m = re.search(r"^conversation_id:\s*(\S+)", p.read_text(errors="replace")[:1000], re.M)
        except OSError:
            continue
        if m:
            ids.add(m.group(1))
    return ids


def triage(convs, min_words, markers):
    print(f"{'date':<10}  {'words':>6}  flag      title  |  first question")
    for c in sorted(convs, key=lambda c: c["date"]):
        w = words(c)
        flag = "short" if w < min_words else ("personal" if is_personal(c, markers) else "")
        print(f"{c['date']:<10}  {w:>6}  {flag:<8}  {c['title'][:50]}  |  {first_question(c)[:70]}")
    keep = [c for c in convs if words(c) >= min_words and not is_personal(c, markers)]
    print(f"\n{len(convs)} conversations, {len(keep)} would import "
          f"(min {min_words} words, personal skipped).")


def do_import(convs, min_words, markers, include_personal) -> dict:
    seen = existing_ids(OUT_ROOT)
    stats = {"written": 0, "short": 0, "personal": 0, "already": 0}
    for c in convs:
        if c["id"] and c["id"] in seen:
            stats["already"] += 1
            continue
        if words(c) < min_words:
            stats["short"] += 1
            continue
        if not include_personal and is_personal(c, markers):
            stats["personal"] += 1
            continue
        d = OUT_ROOT / c["source"]
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{c['date']}-{slug(c['title'])}.md"
        i = 2
        while path.exists():
            path = d / f"{c['date']}-{slug(c['title'])}-{i}.md"
            i += 1
        path.write_text(render(c))
        seen.add(c["id"])
        stats["written"] += 1
    return stats


def promote(paths) -> int:
    PROMOTED.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in map(Path, paths):
        p = p if p.is_absolute() else VAULT / p
        if not p.exists() or OUT_ROOT.resolve() not in p.resolve().parents:
            print(f"skip (not an imported chat): {p}", file=sys.stderr)
            continue
        dst = PROMOTED / p.name
        r = subprocess.run(["git", "-C", str(VAULT), "mv", str(p), str(dst)], capture_output=True)
        if r.returncode != 0:
            shutil.move(str(p), str(dst))
        print(f"promoted: {dst.relative_to(VAULT)}")
        n += 1
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["triage", "import", "promote"])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--min-words", type=int, default=150)
    ap.add_argument("--include-personal", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "promote":
        promote(args.paths)
        return 0
    convs = []
    for p in args.paths:
        convs += parse(json.loads(Path(p).read_text()))
    markers = personal_markers()
    if args.cmd == "triage":
        triage(convs, args.min_words, markers)
        return 0
    s = do_import(convs, args.min_words, markers, args.include_personal)
    print(f"imported {s['written']} into {OUT_ROOT.relative_to(VAULT)}/; skipped {s['short']} short, "
          f"{s['personal']} personal, {s['already']} already imported")
    return 0


if __name__ == "__main__":
    sys.exit(main())
