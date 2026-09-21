#!/usr/bin/env python3
"""Compile the writing map: every long-form asset in the vault, one page.

Writes _Agent-Context/WRITING.md (LLM-owned, regenerated) so the writing agent
in Buzz #writing and the owner see the same inventory: finished pieces, working
files, drafts in flight, the published corpus, the voice and editing files, and
the open pitches. No model call; a plain walk of the folders named in PROFILE.md
(writings_dir, drafts_dir, narratives_dir, editor_dir, longform_dirs, corpus_dirs).

  python3 tools/writing_index.py            # rebuild WRITING.md
  python3 tools/writing_index.py --post     # rebuild and post a short summary to #writing
  python3 tools/writing_index.py --stdout   # print instead of writing
"""
import argparse
from collections import Counter
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from i18n import t  # noqa: E402

STATE_FILE = ".agents/state/writing_pitches.json"


def _title(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            head = fh.read(4000)
    except OSError:
        return path.stem
    m = re.search(r"^title:\s*(.+)$", head, re.M)
    if m:
        return m.group(1).strip().strip('"\'')
    m = re.search(r"^#\s+(.+)$", head, re.M)
    return m.group(1).strip() if m else path.stem


def _words(path):
    try:
        return len(path.read_text(encoding="utf-8", errors="replace").split())
    except OSError:
        return 0


def _private(rel, segments):
    return any(seg and seg in rel for seg in segments)


def _md_files(root, skip=()):
    for p in sorted(root.rglob("*.md")):
        rel = p.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if any(str(rel).startswith(s) for s in skip):
            continue
        yield p


def build(vault, *, writings_dir="Writings", drafts_dir="Writings/Drafts", narratives_dir="Writings/Narratives",
          editor_dir="Writings/Editor", longform_dirs=(), corpus_dirs=(), private_segments=(), lang="en", now=None):
    vault = Path(vault)
    now = now or datetime.now()
    out = []
    out.append(f"# {t('writing.map_title', lang=lang)}")
    out.append("")
    out.append(t("writing.map_intro", lang=lang, stamp=now.strftime("%Y-%m-%d %H:%M")))
    out.append("")

    def table(rows, header):
        out.append(header)
        out.append("|---|---|---|---|")
        for r in rows:
            out.append("| " + " | ".join(r) + " |")
        out.append("")

    # 1. Writings: finished and working files, grouped by folder (drafts and editor listed separately)
    wroot = vault / writings_dir
    groups = {}
    if wroot.is_dir():
        for p in _md_files(wroot, skip=(str(Path(drafts_dir).relative_to(writings_dir)) if drafts_dir.startswith(writings_dir + "/") else "\0",
                                        str(Path(editor_dir).relative_to(writings_dir)) if editor_dir.startswith(writings_dir + "/") else "\0")):
            rel = str(p.relative_to(vault))
            if _private(rel, private_segments) or p.name == ".gitkeep":
                continue
            folder = str(p.relative_to(wroot).parent)
            groups.setdefault(folder, []).append(p)
    out.append(f"## {t('writing.sec_pieces', lang=lang)}")
    out.append("")
    if not groups:
        out.append(t("writing.none", lang=lang)); out.append("")
    for folder, files in sorted(groups.items()):
        out.append(f"### {writings_dir if folder == '.' else writings_dir + '/' + folder}")
        out.append("")
        rows = []
        for p in sorted(files, key=lambda x: x.stat().st_mtime, reverse=True):
            if str(p.relative_to(vault)).startswith(narratives_dir + "/Drafts"):
                continue
            rows.append((f"[[{p.stem}]]", str(p.relative_to(vault)), str(_words(p)),
                         datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")))
        table(rows, f"| {t('writing.col_title', lang=lang)} | {t('writing.col_path', lang=lang)} | {t('writing.col_words', lang=lang)} | {t('writing.col_updated', lang=lang)} |")

    # 2. Drafts in flight
    out.append(f"## {t('writing.sec_drafts', lang=lang)}")
    out.append("")
    rows = []
    for d in (drafts_dir, narratives_dir + "/Drafts"):
        root = vault / d
        if not root.is_dir():
            continue
        for p in _md_files(root):
            head = p.read_text(encoding="utf-8", errors="replace")[:1500]
            status = (re.search(r"^status:\s*(.+)$", head, re.M) or [None, "?"])[1] if re.search(r"^status:\s*(.+)$", head, re.M) else "?"
            rows.append((_title(p), str(p.relative_to(vault)), status.strip(), datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")))
    if rows:
        table(sorted(rows, key=lambda r: r[3], reverse=True), f"| {t('writing.col_title', lang=lang)} | {t('writing.col_path', lang=lang)} | {t('writing.col_status', lang=lang)} | {t('writing.col_updated', lang=lang)} |")
    else:
        out.append(t("writing.none", lang=lang)); out.append("")

    # 3. Open pitches (state written by writing_ideas.py)
    out.append(f"## {t('writing.sec_pitches', lang=lang)}")
    out.append("")
    state_path = vault / STATE_FILE
    pitches = []
    if state_path.exists():
        try:
            pitches = json.loads(state_path.read_text()).get("pitches", [])
        except ValueError:
            pitches = []
    open_p = [p for p in pitches if p.get("status", "open") == "open"]
    if open_p:
        table([(p.get("title", "?"), p.get("venue", "?"), p.get("date", "?"), p.get("path", "")) for p in open_p[-20:]],
              f"| {t('writing.col_title', lang=lang)} | {t('writing.col_venue', lang=lang)} | {t('writing.col_date', lang=lang)} | {t('writing.col_path', lang=lang)} |")
    else:
        out.append(t("writing.none", lang=lang)); out.append("")

    # 4. Long-form homes (e-book, PR pieces and whatever else PROFILE names)
    out.append(f"## {t('writing.sec_longform', lang=lang)}")
    out.append("")
    any_lf = False
    for d in longform_dirs:
        root = vault / d
        if not root.is_dir() or _private(d, private_segments):
            continue
        any_lf = True
        out.append(f"### {d}")
        out.append("")
        rows = [(_title(p), str(p.relative_to(vault)), str(_words(p)), datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d"))
                for p in sorted(_md_files(root), key=lambda x: x.stat().st_mtime, reverse=True)[:25]]
        table(rows, f"| {t('writing.col_title', lang=lang)} | {t('writing.col_path', lang=lang)} | {t('writing.col_words', lang=lang)} | {t('writing.col_updated', lang=lang)} |")
    if not any_lf:
        out.append(t("writing.none", lang=lang)); out.append("")

    # 5. Published corpus (exports such as Medium): counts by year, latest titles
    out.append(f"## {t('writing.sec_corpus', lang=lang)}")
    out.append("")
    any_c = False
    for d in corpus_dirs:
        root = vault / d
        if not root.is_dir():
            continue
        any_c = True
        files = sorted(root.glob("*.md"))
        years = Counter()
        entries = []
        for p in files:
            m = re.match(r"(\d{4})-(\d{2})-(\d{2})_(.+?)(--[0-9a-f]+)?$", p.stem)
            if m:
                years[m.group(1)] += 1
                entries.append((f"{m.group(1)}-{m.group(2)}-{m.group(3)}", m.group(4).replace("-", " ").strip()))
            else:
                entries.append(("", p.stem))
        out.append(f"### {d}")
        out.append("")
        out.append(t("writing.corpus_line", lang=lang, count=len(files),
                     years=", ".join(f"{y}: {n}" for y, n in sorted(years.items()))))
        out.append("")
        for date, title in sorted(entries, reverse=True)[:15]:
            out.append(f"- {date} {title}".rstrip())
        out.append("")
    if not any_c:
        out.append(t("writing.none", lang=lang)); out.append("")

    # 6. Voice and editing files
    out.append(f"## {t('writing.sec_editor', lang=lang)}")
    out.append("")
    eroot = vault / editor_dir
    if eroot.is_dir():
        for p in _md_files(eroot, skip=("Resources",)):
            out.append(f"- `{p.relative_to(vault)}`")
        lint = eroot / "editor_lint.py"
        if lint.exists():
            out.append(f"- `{lint.relative_to(vault)}` ({t('writing.lint_note', lang=lang)})")
    else:
        out.append(t("writing.none", lang=lang))
    out.append("")
    return "\n".join(out).rstrip() + "\n"


def summary_line(text, lang):
    pieces = len(re.findall(r"^\| \[\[", text, re.M))
    drafts = text.split("## ")[2].count("\n| ") - 1 if "## " in text else 0
    return t("writing.post_summary", lang=lang, pieces=pieces, drafts=max(drafts, 0))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--post", action="store_true", help="post a short summary to Buzz #writing")
    ap.add_argument("--stdout", action="store_true")
    args = ap.parse_args()
    import owner_profile as o
    vault = Path(o.VAULT)
    text = build(vault, writings_dir=o.WRITINGS_DIR, drafts_dir=o.DRAFTS_DIR, narratives_dir=o.NARRATIVES_DIR,
                 editor_dir=o.EDITOR_DIR, longform_dirs=o.LONGFORM_DIRS, corpus_dirs=o.CORPUS_DIRS,
                 private_segments=o.PRIVATE_SEGMENTS, lang=o.LANG)
    fm = f"---\nlang: {o.LANG}\nsummary_en: Compiled map of every long-form writing asset in the vault for the Buzz writing agent; regenerated by tools/writing_index.py.\ngenerated: {datetime.now().strftime('%Y-%m-%dT%H:%M')}\n---\n\n"
    if args.stdout:
        print(fm + text)
        return
    from today_queue import atomic_write
    target = vault / "_Agent-Context" / "WRITING.md"
    atomic_write(target, fm + text)
    print(f"wrote {target.relative_to(vault)}")
    if args.post:
        from buzz_delivery import send
        send("writing", summary_line(text, o.LANG) + "\n" + t("writing.post_path", lang=o.LANG, path="_Agent-Context/WRITING.md"),
             key=f"writing-index:{datetime.now().strftime('%Y-%m-%d')}")
        print("queued to #writing")


if __name__ == "__main__":
    main()
