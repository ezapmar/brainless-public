#!/usr/bin/env python3
"""Pitch round for the writing channel (worker, every second month).

Reads what the vault has been thinking about (beliefs, seed ideas, the latest
dialectic syntheses, the week's captures) and the open pitches, then asks the
model for three long-form pitches: title, two-sentence synopsis, venue, the vault
notes each one stands on, and why now. Each pitch becomes one file in the drafts
folder and one root message in Buzz #writing, so the owner and the writing agent
continue in that thread. With --topics the owner's own ideas are turned into
pitches instead of free generation. Never publishes; only proposes.

  python3 tools/writing_ideas.py                       # three pitches
  python3 tools/writing_ideas.py --topics "a" "b"      # one pitch per given topic
  python3 tools/writing_ideas.py --dry-run             # print, write nothing
"""
import argparse
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from i18n import t  # noqa: E402
import owner_profile as o  # noqa: E402

VAULT = Path(o.VAULT)
STATE_FILE = VAULT / ".agents/state/writing_pitches.json"
VENUES = ("hbr", "medium", "blog", "ebook")


def read(path, limit):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def recent(dirname, days, limit=8):
    root = VAULT / dirname
    if not root.is_dir():
        return []
    cutoff = datetime.now() - timedelta(days=days)
    files = [p for p in root.glob("*.md") if datetime.fromtimestamp(p.stat().st_mtime) >= cutoff]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:limit]


def material():
    parts = []
    for d, label, lim in (("Thinking/Beliefs", "beliefs", 700), ("Thinking/Ideas", "ideas", 500)):
        root = VAULT / d
        if root.is_dir():
            parts.append(f"<{label}>")
            for p in sorted(root.glob("*.md"))[:20]:
                parts.append(f"### {p.relative_to(VAULT)}\n{read(p, lim)}")
            parts.append(f"</{label}>")
    synth = sorted((VAULT / ".wiki/digests/queries").glob("*dialectic*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:4]
    if synth:
        parts.append("<dialectic>")
        parts.extend(f"### {p.relative_to(VAULT)}\n{read(p, 1800)}" for p in synth)
        parts.append("</dialectic>")
    week = recent("Thinking/Daily", 14) + recent("Inbox/Spiky", 14, 4)
    if week:
        parts.append("<recent>")
        parts.extend(f"### {p.relative_to(VAULT)}\n{read(p, 900)}" for p in week)
        parts.append("</recent>")
    wmap = VAULT / "_Agent-Context/WRITING.md"
    if wmap.exists():
        parts.append("<writing_map>\n" + read(wmap, 6000) + "\n</writing_map>")
    return "\n\n".join(parts)


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {"pitches": []}


def prompt(topics, previous):
    guide = read(VAULT / o.EDITOR_DIR / "Rehberler" / "uretim-rehberi.md", 2500) or read(VAULT / o.EDITOR_DIR / "README.md", 1500)
    ask = (t("writing.prompt_topics", count=len(topics)) + "\n" + "\n".join(f"- {x}" for x in topics)) if topics else t("writing.prompt_free")
    return f"""You propose long-form pieces for {o.OWNER} (essays for business publications, Medium posts, blog posts, book chapters). Not social media.
{ask}
Rules: each pitch must stand on at least one vault note (cite the exact path); no pitch may repeat an open one; venue is one of {', '.join(VENUES)}; synopsis is two sentences, the first names the problem, the second the piece's own proposal. Testimony over authority: prefer angles where {o.OWNER}'s own experience is the evidence. Never use em dashes or en dashes.
{o.output_lang_directive()}
Answer ONLY with a JSON array of objects with keys: title, synopsis, venue, category, sources (list of vault paths), why_now, opening_scene (one sentence).

<voice>
{guide}
</voice>
<open_pitches>
{json.dumps(previous, ensure_ascii=False)[:3000]}
</open_pitches>
<material>
{material()}
</material>"""


def parse(out):
    m = re.search(r"\[.*\]", out or "", re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return []
    good = []
    for it in data if isinstance(data, list) else []:
        if not isinstance(it, dict) or not it.get("title") or not it.get("synopsis"):
            continue
        it["venue"] = it.get("venue") if it.get("venue") in VENUES else "medium"
        it["sources"] = [s for s in it.get("sources", []) if isinstance(s, str)][:6]
        for k in ("title", "synopsis", "why_now", "opening_scene", "category"):
            it[k] = str(it.get(k, "")).replace(chr(0x2014), ", ").replace(chr(0x2013), "-").strip()
        good.append(it)
    return good[:5]


def slug(title):
    s = re.sub(r"[^\w\s-]", "", title.lower(), flags=re.U)
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return (s[:48] or hashlib.sha256(title.encode()).hexdigest()[:8]).rstrip("-")


def render(p, stamp):
    src = "\n".join(f"- [[{Path(s).stem}]] (`{s}`)" for s in p["sources"]) or "-"
    return (f"---\ntitle: {p['title']}\nkind: pitch\nvenue: {p['venue']}\nstatus: idea\nlang: {o.LANG}\ncreated: {stamp}\n"
            f"summary_en: Long-form pitch proposed by writing_ideas.py; the owner decides in Buzz #writing.\nsources: {json.dumps(p['sources'], ensure_ascii=False)}\n---\n\n"
            f"# {p['title']}\n\n{t('writing.pitch_venue')}: {p['venue']} ({p.get('category') or '-'})\n\n{t('writing.pitch_synopsis')}: {p['synopsis']}\n\n"
            f"{t('writing.pitch_scene')}: {p.get('opening_scene') or '-'}\n\n{t('writing.pitch_why')}: {p.get('why_now') or '-'}\n\n{t('writing.pitch_sources')}:\n{src}\n")


def message(p, path):
    src = ", ".join(f"`{s}`" for s in p["sources"]) or "-"
    return (f"{p['title']} | pitch | {p['venue']} | {t('writing.status_idea')}\n\n{p['synopsis']}\n\n"
            f"{t('writing.pitch_why')}: {p.get('why_now') or '-'}\n{t('writing.pitch_sources')}: {src}\n{t('writing.pitch_file')}: {path}\n\n{t('writing.pitch_footer')}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--topics", nargs="+", help="the owner's own topics; one pitch each")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    state = load_state()
    previous = [{"title": p["title"], "venue": p.get("venue")} for p in state["pitches"] if p.get("status", "open") == "open"]
    from llm import run_prompt
    out = run_prompt(prompt(args.topics or [], previous), timeout=420, lane="writing-ideas")
    pitches = parse(out)
    if not pitches:
        print("no pitches parsed; nothing written", file=sys.stderr)
        return 1
    stamp = datetime.now().strftime("%Y-%m-%d")
    if args.dry_run:
        for p in pitches:
            print(message(p, f"{o.DRAFTS_DIR}/{stamp} pitch-{slug(p['title'])}.md"), "\n---")
        return 0
    from today_queue import atomic_write
    from buzz_delivery import send
    out_dir = VAULT / o.DRAFTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in pitches:
        rel = f"{o.DRAFTS_DIR}/{stamp} pitch-{slug(p['title'])}.md"
        atomic_write(VAULT / rel, render(p, stamp))
        key = "writing-pitch:" + hashlib.sha256(rel.encode()).hexdigest()[:16]
        send("writing", message(p, rel), key=key)
        state["pitches"].append({"title": p["title"], "venue": p["venue"], "date": stamp, "path": rel, "status": "open", "key": key})
        print(f"pitch: {p['title']} -> {rel}")
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(STATE_FILE, json.dumps(state, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
