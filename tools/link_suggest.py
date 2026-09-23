#!/usr/bin/env python3
"""link_suggest.py: pages that mean the same thing but do not link. Propose, never write.

Four in ten wiki pages have no link in or out. The linker ties a summary to the
people and companies it names; it cannot see that a Spiky meeting and a
partnership note are about the same integration when they share no name.
The embedding index can: two pages whose passages sit close together are
about the same thing, whatever words they use.

A pair is proposed only when both tests hold:
  mutual     each page is among the other's K nearest pages. One-sided
             nearness is mostly a hub page (a person, a long raw import)
             that is near everything.
  top 1%     the pair's similarity is above the 99th percentile of all page
             pairs in this run. e5 scores are compressed (random pairs sit
             around 0.85), so a fixed cutoff would mean a different thing
             for every model; a percentile does not.
and the two pages are not already linked in either direction (the same
link_graph lint and prune use).

A page is represented by its first passage: title, summary_en and the opening
of the body. Of first passage, mean of three and mean of all, it recovered
the most existing links (2026-09-23 calibration).

Three sections, most useful first:
  orphan homes    a page with no links, and where it belongs
  new links       two connected pages that should also meet
  near-identical  similarity >= 0.975, or raw twins by name (a summary and
                  its _raw import): one source compiled twice, a meeting
                  captured twice, a template sibling. A merge candidate
                  for wiki_dedupe's procedure, not a link.

Within the first two, pairs that cross shelves (source folders, one inside
the other counting as one shelf, or query commands) come first: two people on one team or two dialectic rounds sit
close because they share a template, and linking them adds little.

Report: .wiki/_link-suggestions.md (gitignored and out of the graph, like the
lint report). Needs the semantic index (tools/semantic_index.py).

Usage:
  python3 tools/link_suggest.py            # write the report
  python3 tools/link_suggest.py --json     # machine output, no report
"""
import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lint_wiki import VAULT, WIKI, all_wiki_files, link_graph  # noqa: E402

REPORT = WIKI / "_link-suggestions.md"
K = 5
PERCENTILE = 99
NEAR_IDENTICAL = 0.975
LIMIT = 40  # per section; a longer list is not read


def _rel(p) -> str:
    return os.path.relpath(p, VAULT)


def _base(rel: str) -> str:
    # Spiky names one meeting twice, with and without the start time
    # (2025-10-10-1458-x and 2025-10-10-x); the time is not part of the name.
    stem = re.sub(r"(\d{4}-\d{2}-\d{2})-\d{4}-", r"\1-", Path(rel).stem)
    return stem.removesuffix("_raw")


def twins(a: str, b: str) -> bool:
    """A summary and its raw import, or one source compiled under two names:
    the flattened compiler repeats the file name after the folder, so one base
    name contains the other."""
    x, y = _base(a), _base(b)
    return x.startswith(y) or y.startswith(x)


def shelf(rel: str) -> str:
    """The shelf a page sits on: the folder of its source for a summary, the
    command for a filed query, else its wiki folder. Two pages on one shelf
    often share a template (two people on a team, two lab reports), so their
    nearness says less than a pair that crosses shelves."""
    p = VAULT / rel
    parent = Path(rel).parent.as_posix()
    if parent.endswith("digests/queries"):
        return parent + "/" + Path(rel).stem[11:].split("-")[0]
    try:
        head = p.read_text(errors="ignore")[:2000]
    except OSError:
        return parent
    m = re.search(r"^source:\s*(.+)$", head, re.M)
    return str(Path(m.group(1).strip().strip('"')).parent) if m else parent


def same_shelf(a: str, b: str) -> bool:
    x, y = shelf(a), shelf(b)
    return x == y or x.startswith(y + "/") or y.startswith(x + "/")


def neighbours(files=None):
    """(pages, similarity matrix, set of linked pairs) or None without an index."""
    import numpy as np
    import semantic_index
    if not semantic_index.available():
        return None
    idx = semantic_index.Index()
    if not idx.load():
        return None
    files = files if files is not None else [
        p for p in all_wiki_files() if p.name != "INDEX.md" and "_commands" not in p.parts]
    graph = link_graph(files)
    linked = set()
    degree = {}
    for a, b in graph["edges"]:
        if a != b:
            ra, rb = _rel(a), _rel(b)
            linked.add((ra, rb))
            linked.add((rb, ra))
            degree[ra] = degree.get(ra, 0) + 1
            degree[rb] = degree.get(rb, 0) + 1
    first = {}
    for i, (rel, j) in enumerate(idx.meta["rows"]):
        if j == 0:
            first[rel] = i
    wanted = {_rel(p) for p in files}
    pages = sorted(r for r in first if r in wanted)
    if len(pages) < 3:
        return None
    m = idx.vecs[[first[r] for r in pages]].astype(np.float32)
    m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-9
    sim = m @ m.T
    np.fill_diagonal(sim, -1.0)
    return pages, sim, linked, degree


def suggest(k: int = K, percentile: float = PERCENTILE, files=None) -> dict | None:
    import numpy as np
    got = neighbours(files)
    if got is None:
        return None
    pages, sim, linked, degree = got
    cut = float(np.percentile(sim[np.triu_indices(len(pages), 1)], percentile))
    top = [set(np.argsort(-sim[i])[:k].tolist()) for i in range(len(pages))]
    orphan, links, dupes = [], [], []
    for i, a in enumerate(pages):
        for j in top[i]:
            if j <= i or i not in top[j] or sim[i, j] < cut:
                continue
            b = pages[j]
            if (a, b) in linked:
                continue
            row = {"a": a, "b": b, "sim": round(float(sim[i, j]), 3),
                   "same_shelf": same_shelf(a, b)}
            if row["sim"] >= NEAR_IDENTICAL or twins(a, b):
                dupes.append(row)
            elif not degree.get(a) or not degree.get(b):
                # The orphan goes first: it is the page that needs a home.
                if degree.get(a):
                    row["a"], row["b"] = b, a
                orphan.append(row)
            else:
                links.append(row)
    # Pairs that cross shelves first: they join clusters instead of restating one.
    for lst in (orphan, links):
        lst.sort(key=lambda r: (r["same_shelf"], -r["sim"]))
    dupes.sort(key=lambda r: -r["sim"])
    return {"date": date.today().isoformat(), "pages": len(pages), "cutoff": round(cut, 3),
            "k": k, "orphan_homes": orphan, "new_links": links, "near_identical": dupes}


def _name(rel: str) -> str:
    return Path(rel).stem


def markdown(res: dict) -> str:
    out = [
        "---", "lang: en",
        "summary_en: Proposed wikilinks from the semantic index, mutual nearest pages above "
        "the 99th percentile that do not link yet. Report only; nothing is applied.",
        "---", "",
        f"# Link suggestions ({res['date']})", "",
        f"{res['pages']} pages. A pair is listed when each page is among the other's "
        f"{res['k']} nearest and the similarity is at least {res['cutoff']} (top 1% of pairs). "
        "Names are in backticks, not links, so this report adds no edges. "
        "Generated by tools/link_suggest.py.", "",
    ]
    sections = (
        ("Orphan homes", "orphan_homes", "The first page has no links yet; the second is where it belongs."),
        ("New links", "new_links", "Both pages are connected elsewhere, but not to each other."),
        ("Near-identical", "near_identical",
         "Probably one page twice (a summary and its raw import, a meeting captured twice) or a "
         "template sibling. Merge candidates for the wiki_dedupe procedure, not links."),
    )
    for title, key, note in sections:
        rows = res[key]
        out += [f"## {title} ({len(rows)})", "", note, ""]
        for r in rows[:LIMIT]:
            shelf = ", same shelf" if r.get("same_shelf") and key != "near_identical" else ""
            out.append(f"- `{_name(r['a'])}` and `{_name(r['b'])}` ({r['sim']}{shelf})")
        if len(rows) > LIMIT:
            out.append(f"- ... {len(rows) - LIMIT} more in `--json`")
        out.append("")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--k", type=int, default=K)
    args = ap.parse_args(argv)
    res = suggest(k=args.k)
    if res is None:
        print("no semantic index: python3 tools/semantic_index.py build")
        return 0
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        REPORT.write_text(markdown(res))
        print(f"{_rel(REPORT)}: {len(res['orphan_homes'])} orphan homes, "
              f"{len(res['new_links'])} new links, {len(res['near_identical'])} near-identical")
    print(f"RUNLOG orphan_homes={len(res['orphan_homes'])} new_links={len(res['new_links'])} "
          f"near_identical={len(res['near_identical'])}", file=sys.stderr if args.json else sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
