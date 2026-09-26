#!/usr/bin/env python3
"""contradiction_check.py: does tonight's new source disagree with the vault?

A concept page keeps disagreements side by side, but only for the summaries
assigned to it, and most summaries feed no concept. A new article that says
the opposite of an old note, a decision or a project page went unnoticed. So
after each compile, every new or changed summary (at most MAX_PER_NIGHT) is
read against its nearest pages and a model answers one question: does it
contradict any of them, or show one of their claims out of date?

  candidates  the semantic index's nearest pages to the summary: another
              document (not its raw twin, its book folder or a renamed copy,
              which score above SAME_TEXT), still live, above MIN_SIM, at most
              NEIGHBOURS of them; summaries, concepts, projects, entities, ideas
  judge       one model call per summary, JSON out; different emphasis or
              more detail is not a contradiction, and an empty list is the
              normal answer
  record      _Agent-Context/CONTRADICTIONS.md (last KEEP_DAYS, newest first)
              and the change brief's Flagged section; a pair already recorded
              is not recorded again

Nothing on any page changes. The owner decides what a contradiction means.

Usage:
  python3 tools/contradiction_check.py --page .wiki/summaries/x.md [--dry-run]
  python3 tools/contradiction_check.py --since REF [--dry-run]
"""
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import concepts as C  # noqa: E402

VAULT = Path(os.environ.get("BRAINLESS_VAULT") or Path(__file__).resolve().parents[1])
WIKI = VAULT / ".wiki"
REPORT = VAULT / "_Agent-Context" / "CONTRADICTIONS.md"
LANE = "contradiction-judge"
KINDS = ("summaries", "concepts", "projects", "entities", "ideas")
MAX_PER_NIGHT = 8
NEIGHBOURS = 3
MIN_SIM = 0.86      # multilingual-e5-large: related pages sat at 0.86 to 0.89 on 25/09
SAME_TEXT = 0.93    # at or above: another copy of the same text, not another source
PAGE_CHARS = 3500
KEEP_DAYS = 30
_ROW = re.compile(r"^<!-- c:([0-9a-f]{12}) (\d{4}-\d{2}-\d{2}) -->$")


def _rel(p: Path) -> str:
    return p.relative_to(VAULT).as_posix()


def _read(rel: str) -> str:
    try:
        return (VAULT / rel).read_text(errors="replace")
    except OSError:
        return ""


def _source(text: str) -> str:
    fm, _ = C.split_frontmatter(text)
    return C.fm_value(fm, "source") or ""


def _exists(rel: str) -> bool:
    return (VAULT / rel).exists()


def query_text(text: str) -> str:
    fm, body = C.split_frontmatter(text)
    return ((C.fm_value(fm, "summary_en") or "") + "\n" + body[:1500]).strip()


def candidates(rel: str, hits) -> list[tuple[float, str]]:
    """Filter index hits [(sim, rel, passage)] down to the pages worth asking about."""
    own_src = _source(_read(rel))
    own_group = C.source_group(own_src, _exists) if own_src else None
    out = []
    for sim, other, _ in hits:
        parts = Path(other).parts
        if other == rel or len(parts) < 3 or parts[1] not in KINDS or sim >= SAME_TEXT or sim < MIN_SIM:
            continue
        src = _source(_read(other))
        if parts[1] == "summaries":
            if not src or not _exists(src):
                continue          # a summary of a renamed or removed file
            if own_group and C.source_group(src, _exists) == own_group:
                continue
        out.append((sim, other))
        if len(out) >= NEIGHBOURS:
            break
    return out


def prompt(rel: str, text: str, others: list[tuple[str, str]]) -> str:
    from owner_profile import output_lang_directive
    blob = "".join(f"\n--- OLD PAGE {o} ---\n{t[:PAGE_CHARS]}\n" for o, t in others)
    return f"""You check a personal knowledge wiki for contradictions. A NEW page was just
compiled. Compare it with the OLD pages below.

Report only a real conflict:
- contradiction: the new page and an old page make claims that cannot both be true
  (numbers, facts, recommendations that exclude each other, a decision reversed)
- outdated: the new page shows that an old page's claim is no longer true
Different emphasis, more detail, a different topic, or two views of a debated
question that the old page already presents as debated are NOT conflicts. Most
pages have none; an empty list is the normal answer.

Quote each claim briefly in the language of its page. {output_lang_directive()}
Do NOT use em dashes or en dashes.

NEW PAGE {rel}
{text[:PAGE_CHARS]}
{blob}
Output ONLY JSON:
{{"conflicts": [{{"page": "<old page path exactly as given>", "kind": "contradiction|outdated",
  "new": "<the new page's claim>", "old": "<the old page's claim>", "why": "<one sentence>"}}]}}
"""


def parse(out: str | None, allowed: set[str]) -> list[dict] | None:
    """The conflicts in a model reply; None when the reply is not usable."""
    if not out:
        return None
    obj = C.parse_json_object(out)
    if not isinstance(obj, dict) or not isinstance(obj.get("conflicts"), list):
        return None
    res = []
    for c in obj["conflicts"]:
        if not isinstance(c, dict) or c.get("page") not in allowed:
            continue
        kind = c.get("kind") if c.get("kind") in ("contradiction", "outdated") else "contradiction"
        fields = {k: C.strip_dashes(" ".join(str(c.get(k, "")).split()))[:300] for k in ("new", "old", "why")}
        if fields["new"] and fields["old"]:
            res.append({"page": c["page"], "kind": kind, **fields})
    return res


def _ignored(rels) -> set[str]:
    """Pages git ignores (guarded exports): never quoted into a tracked file."""
    import subprocess
    rels = list(rels)
    if not rels:
        return set()
    try:
        r = subprocess.run(["git", "check-ignore", "--stdin"], cwd=VAULT, input="\n".join(rels),
                           capture_output=True, text=True, timeout=30)
        return set(r.stdout.splitlines())
    except Exception:
        return set(rels)        # unsure means guarded


def check(rel: str, run=None, index=None) -> dict:
    """{'page', 'candidates', 'conflicts'} for one summary; conflicts None on failure."""
    text = _read(rel)
    if index is None:
        import semantic_index
        index = semantic_index.Index()
    src = _source(text)
    if _ignored([rel]) or (src and (not _exists(src) or C.skip_reason(src, _exists))):
        # guarded, or a summary of a renamed or filtered-out file
        return {"page": rel, "candidates": [], "conflicts": []}
    hits = index.query(query_text(text), k=25)
    hidden = _ignored(o for _, o, _ in hits)
    cands = candidates(rel, [h for h in hits if h[1] not in hidden])
    res = {"page": rel, "candidates": [o for _, o in cands], "conflicts": []}
    if not cands:
        return res
    if run is None:
        from llm import run_prompt

        def run(p):
            return run_prompt(p, timeout=300, lane=LANE)
    others = [(o, _read(o)) for o in res["candidates"]]
    res["conflicts"] = parse(run(prompt(rel, text, others)), set(res["candidates"]))
    return res


def _key(new: str, c: dict) -> str:
    return hashlib.sha256(f"{new}\0{c['page']}\0{c['new']}".encode()).hexdigest()[:12]


def _title(rel: str) -> str:
    return Path(rel).stem


def record(results: list[dict], now: datetime | None = None) -> list[dict]:
    """Add new conflicts to CONTRADICTIONS.md; returns the ones added."""
    from i18n import t
    now = now or datetime.now()
    text = REPORT.read_text() if REPORT.exists() else ""
    blocks = [b for b in re.split(r"(?m)^(?=<!-- c:)", text) if _ROW.match(b.splitlines()[0] if b else "")]
    cutoff = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    blocks = [b for b in blocks if _ROW.match(b.splitlines()[0]).group(2) >= cutoff]
    seen = {_ROW.match(b.splitlines()[0]).group(1) for b in blocks}
    added, new_blocks = [], []
    day = now.strftime("%Y-%m-%d")
    for r in results:
        for c in r.get("conflicts") or []:
            k = _key(r["page"], c)
            if k in seen:
                continue
            seen.add(k)
            added.append({**c, "new_page": r["page"]})
            new_blocks.append(
                f"<!-- c:{k} {day} -->\n"
                + t(f"contradiction_check.item_{c['kind']}", day=day,
                    new=_title(r["page"]), old=_title(c["page"])) + "\n"
                + f"  - {t('contradiction_check.new_says')}: {c['new']}\n"
                + f"  - {t('contradiction_check.old_says')}: {c['old']}\n"
                + f"  - {t('contradiction_check.why')}: {c['why']}\n")
    head = [t("contradiction_check.title"), "", t("contradiction_check.intro", days=KEEP_DAYS), ""]
    body = "\n".join(b.rstrip("\n") for b in new_blocks + blocks)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(C.strip_dashes("\n".join(head) + (body + "\n" if body else t("contradiction_check.none") + "\n")))
    return added


def brief_lines(added: list[dict]) -> list[tuple[str, str]]:
    """(page title, text) pairs for the change brief's Flagged section."""
    return [(_title(a["new_page"]), f"[[{_title(a['page'])}]]: {a['new']} / {a['old']}") for a in added]


def run_night(pages: list[str], run=None, index=None, deadline=None) -> tuple[list[dict], dict]:
    """Check up to MAX_PER_NIGHT pages before `deadline` (time.monotonic());
    returns (added conflicts, counts)."""
    import time
    counts = {"checked": 0, "conflicts": 0, "failed": 0}
    results = []
    for rel in pages[:MAX_PER_NIGHT]:
        if deadline is not None and time.monotonic() > deadline - 300:
            print("contradiction check: out of time")
            break
        r = check(rel, run=run, index=index)
        if r["candidates"]:
            counts["checked"] += 1
        if r["conflicts"] is None:
            counts["failed"] += 1
            continue
        results.append(r)
    added = record(results)
    counts["conflicts"] = len(added)
    return added, counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--page", help="one wiki page, vault-relative")
    g.add_argument("--since", help="every summary added or changed since this git ref")
    ap.add_argument("--dry-run", action="store_true", help="show the candidates, ask no model")
    args = ap.parse_args(argv)
    if args.page:
        pages = [args.page]
    else:
        import wiki_changes
        pages = wiki_changes.changed_pages(wiki_changes.snapshot_at(args.since), wiki_changes.snapshot(),
                                           "summaries")
    if args.dry_run:
        import semantic_index
        idx = semantic_index.Index()
        for rel in pages[:MAX_PER_NIGHT]:
            r = check(rel, run=lambda p: '{"conflicts": []}', index=idx)
            print(rel + ("" if r["candidates"] else "  (skipped: no candidates, guarded or filtered)"))
            for o in r["candidates"]:
                print(f"  {o}")
        return 0
    added, counts = run_night(pages)
    for a in added:
        print(f"{a['kind']}: {a['new_page']} vs {a['page']}: {a['why']}")
    print("RUNLOG " + " ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
