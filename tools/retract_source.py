#!/usr/bin/env python3
"""retract_source.py: take a bad source back out of the wiki.

Compiling folds a source into many pages: its summary, the concept pages it
feeds, links from other pages, contradiction entries. A source found wrong a
month later has touched all of them. This traces one source through the wiki
and, when told to, takes it back out.

  trace   (default, changes nothing) every summary of the document (its raw
          twin and book folder included), the concept pages that cite it,
          other pages that link to it, approved link rows and contradiction
          entries
  apply   --apply --reason "...":
          - the document goes into _Agent-Context/retracted.md, and the
            compiler never compiles it again (concepts.skip_reason)
          - its summaries move to .wiki/_archive/summaries/, logged
          - each concept page is rewritten by the model: a claim that rests
            only on this source is struck and moved to Superseded as
            retracted, with the reason; a claim with other sources keeps them.
            The validator refuses a rewrite that loses any other citation or
            struck line; that page is left as it was and named for a hand edit
          - links from other pages become plain text, link rows are marked
            `retracted`, contradiction entries that cite it are removed

The source file itself is left where it is: it sits in a human folder.
Undo: delete the row in retracted.md and `git revert` the commit.

Usage:
  python3 tools/retract_source.py "Library/Articles/x.md"
  python3 tools/retract_source.py "Library/Articles/x.md" --apply --reason "study was retracted"
"""
import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import concepts as C  # noqa: E402

VAULT = Path(os.environ.get("BRAINLESS_VAULT") or Path(__file__).resolve().parents[1])
WIKI = VAULT / ".wiki"
REGISTRY = VAULT / "_Agent-Context" / "retracted.md"
LANE = "compile-concept"
REGISTRY_HEAD = """# Retracted sources

Written by tools/retract_source.py. A document listed here is never compiled
again, and its summaries sit in .wiki/_archive/summaries/. The file itself is
left where it is. To undo, delete the row and revert the retraction commit.

| Date | Source | Reason |
|---|---|---|
"""
_ROW = re.compile(r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*(.+?)\s*\|\s*(.*?)\s*\|\s*$")


def _exists(rel: str) -> bool:
    return (VAULT / rel).exists()


def rows() -> list[dict]:
    try:
        text = REGISTRY.read_text()
    except OSError:
        return []
    return [{"date": m[1], "source": m[2].strip("`"), "reason": m[3]}
            for m in map(_ROW.match, text.splitlines()) if m]


def groups() -> set[str]:
    """source_group keys of every retracted document."""
    return {C.source_group(r["source"], _exists) for r in rows()}


def _fm_source(p: Path) -> str:
    fm, _ = C.split_frontmatter(C.read_page(p))
    return C.fm_value(fm, "source") or ""


def _live_pages():
    for p in WIKI.rglob("*.md"):
        parts = p.relative_to(WIKI).parts
        if parts[0] in ("_archive", "_index", "_commands") or p.name in ("INDEX.md", "Home.md"):
            continue
        yield p


def _cites(text: str, stems: set[str]) -> set[str]:
    return C.cited_links(text) & stems


def trace(source: str) -> dict:
    """What the document reaches. `source` is vault-relative."""
    source = C._norm(source)
    group = C.source_group(source, _exists)
    summaries = sorted(p for p in (WIKI / "summaries").glob("*.md")
                       if _fm_source(p) and C.source_group(_fm_source(p), _exists) == group)
    stems = {p.stem for p in summaries}
    concepts, others = [], []
    for p in _live_pages():
        if p in summaries:
            continue
        text = C.read_page(p)
        hit = _cites(text, stems) | (set(C.read_members(text)) & stems)
        if hit:
            (concepts if p.parent == WIKI / "concepts" else others).append((p, sorted(hit)))
    link_rows = []
    try:
        from compile_resources import link_registry
        paths = {s.relative_to(VAULT).as_posix() for s in summaries}
        link_rows = [r for r in link_registry() if r["a"] in paths or r["b"] in paths]
    except Exception:
        pass
    contradictions = 0
    creport = VAULT / "_Agent-Context" / "CONTRADICTIONS.md"
    if creport.exists():
        contradictions = sum(1 for s in stems if f"[[{s}]]" in creport.read_text())
    return {"source": source, "group": group, "summaries": summaries, "stems": stems,
            "concepts": concepts, "others": others, "link_rows": link_rows,
            "contradictions": contradictions}


def concept_prompt(page: str, stems: set[str], reason: str, month: str) -> str:
    from owner_profile import output_lang_directive
    cites = ", ".join(f"[[{s}]]" for s in sorted(stems))
    return f"""You maintain a concept page in a personal knowledge wiki. The owner has RETRACTED
a source: {cites}. Reason: {reason}

Rewrite the page so it no longer rests on that source:
- A claim supported ONLY by the retracted source: remove it from where it stands and add
  it under the Superseded heading as: ~~claim~~ retracted {month}: source withdrawn ({reason}).
  Do not link the retracted source there or anywhere else.
- A claim also supported by another cited source: keep it, drop only the retracted citation.
- A Contested entry where one side rests only on the retracted source: remove that side.
  If two or more sides remain, keep the entry. If one side remains, it is no longer
  contested: move that side's claim, with its citation and date, into the current
  position section. Never drop a claim a remaining source makes.
- Remove the retracted source from Related and every other list.
- Keep every other [[link]], every existing ~~struck~~ line exactly, the headings and the
  frontmatter. Add nothing new. Do NOT use em dashes or en dashes.
- {output_lang_directive()}

Return ONLY the full updated page.

PAGE:
{page}
"""


def _unlink(text: str, stems: set[str]) -> str:
    """[[stem|alias]] -> alias, [[stem]] -> stem, for the retracted stems only."""
    pat = re.compile(r"\[\[(" + "|".join(re.escape(s) for s in stems) + r")(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]")
    return pat.sub(lambda m: m.group(2) or m.group(1), text)


def _set_members(text: str, drop: set[str]) -> str:
    members = C.read_members(text)
    if not set(members) & drop:
        return text
    return C.set_fm_key(text, "members", C.members_value({k: v for k, v in members.items() if k not in drop}))


def apply(tr: dict, reason: str, run=None, now: datetime | None = None) -> dict:
    """Take the traced document out. Returns what was done and what was left."""
    from today_queue import atomic_write
    now = now or datetime.now()
    day, month = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
    reason = " ".join(reason.replace("|", "/").split())
    stems = tr["stems"]
    done = {"archived": 0, "concepts": [], "hand_edit": [], "unlinked": 0, "link_rows": 0}

    # 1. The registry first: whatever fails below, the compiler stops using it.
    if tr["source"] not in {r["source"] for r in rows()}:
        head = REGISTRY.read_text() if REGISTRY.exists() else REGISTRY_HEAD
        atomic_write(REGISTRY, head.rstrip("\n") + f"\n| {day} | `{tr['source']}` | {reason} |\n")

    # 2. Concept pages, one model call each, guarded by the validator.
    if run is None:
        from llm import run_prompt

        def run(p):
            # A long concept page takes minutes to rewrite whole (3.5 on 25/09).
            return run_prompt(p, timeout=600, lane=LANE)
    for p, _ in tr["concepts"]:
        old = C.read_page(p)
        out = run(concept_prompt(old, stems, reason, month))
        new = C.strip_dashes((out or "").strip()) + "\n" if out else ""
        if new and not new.startswith("---\n"):
            m = re.search(r"(?m)^---\n(?=[a-z_]+:)", new)
            new = new[m.start():] if m else new
        problems = C.validate_update(old, new, min_ratio=0.4, retracted=frozenset(stems)) if new else ["no reply"]
        if problems:
            done["hand_edit"].append((p.relative_to(VAULT).as_posix(), problems[0]))
            continue
        atomic_write(p, _set_members(new, stems))
        done["concepts"].append(p.relative_to(VAULT).as_posix())

    # 3. Other pages: plain text where the link was.
    for p, _ in tr["others"]:
        text = C.read_page(p)
        new = _set_members(_unlink(text, stems), stems)
        if new != text:
            atomic_write(p, new)
            done["unlinked"] += 1

    # 4. Approved link rows stop being replayed.
    try:
        from compile_resources import LINK_REGISTRY
        if tr["link_rows"] and LINK_REGISTRY.exists():
            paths = {s.relative_to(VAULT).as_posix() for s in tr["summaries"]}
            lines = []
            for line in LINK_REGISTRY.read_text().splitlines():
                cells = [c.strip() for c in line.split("|")]
                if len(cells) > 4 and (cells[1] in paths or cells[2] in paths) and cells[3] in ("link", "merge"):
                    cells[3] = "retracted"
                    line = "| " + " | ".join(cells[1:-1]) + " |"
                    done["link_rows"] += 1
                lines.append(line)
            atomic_write(LINK_REGISTRY, "\n".join(lines) + "\n")
    except Exception as e:
        print(f"link registry left as it was: {e}")

    # 5. Contradiction entries that cite it.
    creport = VAULT / "_Agent-Context" / "CONTRADICTIONS.md"
    if creport.exists() and stems:
        blocks = re.split(r"(?m)^(?=<!-- c:)", creport.read_text())
        kept = [b for b in blocks if not any(f"[[{s}]]" in b for s in stems)]
        if len(kept) != len(blocks):
            atomic_write(creport, "".join(kept))

    # 6. The summaries last, into the archive with the reason. Not while a
    # concept page still cites them: that page would point at nothing, and a
    # run after the hand edit finds them again and finishes the job.
    if done["hand_edit"]:
        return done
    arch = WIKI / "_archive" / "summaries"
    arch.mkdir(parents=True, exist_ok=True)
    lines = []
    for s in tr["summaries"]:
        to = arch / s.name
        if to.exists():      # an older copy is archived under this name already
            to = arch / f"{s.stem}.retracted-{now.strftime('%Y%m%d')}.md"
        if s.exists() and not to.exists():
            s.replace(to)
            done["archived"] += 1
            lines.append(f"| {day} | retract | {s.relative_to(VAULT)} | {to.relative_to(VAULT)} | {reason} |")
    try:
        from compile_resources import _archive_log
        _archive_log(lines)
    except Exception as e:
        print(f"archive log not written: {e}")
    return done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("source", help="vault-relative path of the source file")
    ap.add_argument("--apply", action="store_true", help="take it out (default: trace only)")
    ap.add_argument("--reason", help="why; required with --apply, written to the registry and the pages")
    args = ap.parse_args(argv)
    if args.apply and not (args.reason or "").strip():
        ap.error("--apply needs --reason")
    tr = trace(args.source)
    rel = lambda p: p.relative_to(VAULT).as_posix()  # noqa: E731
    print(f"{tr['source']}  (document: {tr['group']})")
    print(f"summaries ({len(tr['summaries'])}):")
    for s in tr["summaries"]:
        print(f"  {rel(s)}")
    print(f"concept pages to rewrite ({len(tr['concepts'])}):")
    for p, hit in tr["concepts"]:
        print(f"  {rel(p)}  cites {', '.join(hit)}")
    print(f"other pages to unlink ({len(tr['others'])}):")
    for p, _ in tr["others"]:
        print(f"  {rel(p)}")
    print(f"approved link rows: {len(tr['link_rows'])}; contradiction entries: {tr['contradictions']}")
    if not tr["summaries"] and not tr["concepts"] and not tr["others"]:
        print("nothing in the wiki rests on this source")
    if not args.apply:
        print("\ntrace only; add --apply --reason \"...\" to take it out")
        return 0
    done = apply(tr, args.reason)
    print(f"\nretracted: {done['archived']} summaries archived, {len(done['concepts'])} concept page(s) "
          f"rewritten, {done['unlinked']} page(s) unlinked, {done['link_rows']} link row(s) marked")
    for page, why in done["hand_edit"]:
        print(f"  HAND EDIT {page}: {why}")
    if done["hand_edit"]:
        print("summaries kept until those pages are done; run the same command again after the edit")
    return 1 if done["hand_edit"] else 0


if __name__ == "__main__":
    sys.exit(main())
