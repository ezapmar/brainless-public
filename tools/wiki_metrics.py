#!/usr/bin/env python3
"""wiki_metrics.py: four numbers that say whether the wiki is a graph or a folder.

Using the vault day to day does not tell you whether linking still works. The
failure is silent: ingestion keeps writing pages, stops connecting them, and a
growing share of the wiki becomes unreachable by following links. These
numbers make the direction visible, week on week (thresholds from the Second
Brain OS guide, docs/references.md):

  orphan_rate     pages with no link in or out / pages        healthy < 5%, red > 15%
  avg_degree      links per page (undirected, deduplicated)    healthy 3-8, warn < 2
  main_share      pages in the largest connected component     healthy >= 80%
  stale_concepts  concept pages not compiled for 90+ days       report only

plus the bridges: the pages with the highest betweenness, the few pages that
hold two clusters together. They are the most valuable pages in the vault and
the most fragile, because a wrong merge there silently disconnects two areas.

stdlib only (union-find and Brandes' algorithm); the graph comes from
lint_wiki.link_graph, so every tool agrees on what a link is.

Usage: python3 tools/wiki_metrics.py [--json]
"""
import json
import re
import sys
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lint_wiki import WIKI, all_wiki_files, link_graph, orphan_pages, parse_fm  # noqa: E402

ORPHAN_WARN, ORPHAN_RED = 0.05, 0.15
DEGREE_WARN = 2.0
MAIN_SHARE_WARN = 0.80
STALE_DAYS = 90
BRIDGE_FOLDERS = ("concepts", "entities", "ideas", "projects", "articles")


def nodes_and_edges(files, graph):
    """Pages (INDEX and _commands excluded: they link to everything by design)
    and undirected, deduplicated, loop-free edges between them."""
    nodes = [p for p in files if p.name != "INDEX.md" and "_commands" not in p.parts]
    keep = set(nodes)
    edges = set()
    for a, b in graph["edges"]:
        if a in keep and b in keep and a != b:
            edges.add((a, b) if str(a) < str(b) else (b, a))
    return nodes, edges


def components(nodes, edges) -> list[int]:
    """Component sizes, largest first (union-find)."""
    sizes = {}
    for root in component_ids(nodes, edges).values():
        sizes[root] = sizes.get(root, 0) + 1
    return sorted(sizes.values(), reverse=True)


def component_ids(nodes, edges) -> dict:
    """node -> its component's root (union-find)."""
    parent = {n: n for n in nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    return {n: find(n) for n in nodes}


def betweenness(nodes, edges) -> dict:
    """Brandes' algorithm, unweighted and undirected, normalised to [0, 1]."""
    adj = {n: [] for n in nodes}
    for a, b in edges:
        adj[a].append(b)
        adj[b].append(a)
    bc = dict.fromkeys(nodes, 0.0)
    for s in nodes:
        if not adj[s]:
            continue
        stack, pred = [], {s: []}
        sigma, dist = {s: 1}, {s: 0}
        q = deque([s])
        while q:
            v = q.popleft()
            stack.append(v)
            for w in adj[v]:
                if w not in dist:
                    dist[w] = dist[v] + 1
                    sigma[w] = 0
                    pred[w] = []
                    q.append(w)
                if dist[w] == dist[v] + 1:
                    sigma[w] += sigma[v]
                    pred[w].append(v)
        delta = dict.fromkeys(stack, 0.0)
        while stack:
            w = stack.pop()
            for v in pred[w]:
                delta[v] += sigma[v] / sigma[w] * (1 + delta[w])
            if w != s:
                bc[w] += delta[w]
    n = len(nodes)
    scale = 1 / ((n - 1) * (n - 2)) if n > 2 else 1   # undirected: each pair counted twice
    return {k: v * scale for k, v in bc.items()}


def stale_concepts(files, now) -> tuple[int, int]:
    concepts = [p for p in files if p.parent == WIKI / "concepts"]
    cut = (now - timedelta(days=STALE_DAYS)).strftime("%Y-%m-%d")
    stale = 0
    for p in concepts:
        try:
            fm = parse_fm(p.read_text(errors="replace"))
        except OSError:
            continue
        m = re.match(r"\d{4}-\d{2}-\d{2}", fm.get("compiled_at", ""))
        if not m or m.group(0) < cut:
            stale += 1
    return stale, len(concepts)


def compute(now: datetime | None = None, *, bridges: int = 3) -> dict:
    now = now or datetime.now()
    files = all_wiki_files()
    graph = link_graph(files)
    nodes, edges = nodes_and_edges(files, graph)
    orphans = [p for p in orphan_pages(files, graph) if p in set(nodes)]
    n = len(nodes)
    sizes = components(nodes, edges)
    stale, concept_n = stale_concepts(files, now)
    out = {
        "pages": n,
        "orphan_rate": round(len(orphans) / n, 3) if n else 0.0,
        "avg_degree": round(2 * len(edges) / n, 2) if n else 0.0,
        "main_share": round(sizes[0] / n, 3) if n else 0.0,
        "components": len(sizes),
        "stale_concepts": f"{stale}/{concept_n}",
        "bridges": [],
    }
    if bridges and n > 2:
        bc = betweenness(nodes, edges)
        ranked = sorted((p for p in nodes if p.parent.name in BRIDGE_FOLDERS or
                         p.parent.parent.name == "projects"), key=lambda p: -bc[p])
        out["bridges"] = [p.stem for p in ranked[:bridges] if bc[p] > 0]
    return out


def verdicts(m: dict) -> list[tuple[str, str, str]]:
    """(metric, OK|WARN|RED, why) for the numbers that have thresholds."""
    out = []
    r = m.get("orphan_rate", 0)
    out.append(("orphan_rate", "RED" if r > ORPHAN_RED else "WARN" if r > ORPHAN_WARN else "OK",
                f"{r:.0%} (healthy < {ORPHAN_WARN:.0%})"))
    d = m.get("avg_degree", 0)
    out.append(("avg_degree", "WARN" if d < DEGREE_WARN else "OK", f"{d} (healthy 3 to 8)"))
    s = m.get("main_share", 0)
    out.append(("main_share", "WARN" if s < MAIN_SHARE_WARN else "OK",
                f"{s:.0%} in one component (healthy >= {MAIN_SHARE_WARN:.0%})"))
    return out


def markdown(m: dict) -> list[str]:
    lines = ["## Graph health", "",
             "| Metric | Value | Status |", "|---|---|---|"]
    for name, status, why in verdicts(m):
        lines.append(f"| {name} | {why} | {status} |")
    lines += [f"| components | {m['components']} | |",
              f"| stale concepts (>{STALE_DAYS}d) | {m['stale_concepts']} | |",
              f"| bridges | {', '.join(f'[[{b}]]' for b in m['bridges']) or '-'} | |", ""]
    return lines


def main():
    m = compute()
    if "--json" in sys.argv:
        print(json.dumps(m, ensure_ascii=False))
    else:
        print("\n".join(markdown(m)))


if __name__ == "__main__":
    main()
