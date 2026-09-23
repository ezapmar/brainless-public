#!/usr/bin/env python3
"""graph_export.py: the wiki's link graph as a file Gephi opens ready to look at.

Obsidian's global graph is a hairball past a few hundred pages and computes
nothing. This writes the same graph (lint_wiki.link_graph, so every tool
agrees on what a link is) with what makes a picture worth looking at already
on it: colour by page type, size by link count, and per node the degree,
betweenness (the bridges between clusters), component size and orphan flag,
so Gephi can filter and rank without any setup.

  python3 tools/graph_export.py                      # GEXF, all pages
  python3 tools/graph_export.py --no-summaries       # the concept graph only
  python3 tools/graph_export.py --main-only          # drop the islands
  python3 tools/graph_export.py --format graphml     # for other tools

In Gephi: File > Open, then Layout > ForceAtlas 2 (scaling 10, prevent
overlap on), then Preview. Colours and sizes come from the file.

Output goes to logs/graph/ (gitignored): node labels are real names, so the
file stays on this machine. The markdown stays canonical; this is a snapshot,
regenerated rather than edited.
"""
import argparse
import math
import sys
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lint_wiki import VAULT, WIKI, all_wiki_files, link_graph, orphan_pages, parse_fm  # noqa: E402
import wiki_metrics as M  # noqa: E402

# Colour-blind-safe (Okabe-Ito), one per page type.
COLOURS = {
    "concept": (230, 159, 0), "entity": (0, 114, 178), "project": (0, 158, 115),
    "idea": (204, 121, 167), "summary": (170, 170, 170), "digest": (86, 180, 233),
    "query": (213, 94, 0), "moc": (240, 228, 66), "other": (110, 110, 110),
}


def page_type(p: Path) -> str:
    parts = p.relative_to(WIKI).parts
    top = parts[0]
    if top == "digests":
        return "query" if len(parts) > 2 and parts[1] == "queries" else "digest"
    return {"concepts": "concept", "articles": "concept", "entities": "entity", "projects": "project",
            "ideas": "idea", "summaries": "summary", "moc": "moc"}.get(top, "other")


def build(no_summaries=False, main_only=False):
    files = all_wiki_files()
    graph = link_graph(files)
    nodes, edges = M.nodes_and_edges(files, graph)
    if no_summaries:
        nodes = [n for n in nodes if page_type(n) != "summary"]
        keep = set(nodes)
        edges = {e for e in edges if e[0] in keep and e[1] in keep}
    comp = M.component_ids(nodes, edges)
    size_of = {}
    for root in comp.values():
        size_of[root] = size_of.get(root, 0) + 1
    if main_only and size_of:
        main = max(size_of, key=size_of.get)
        nodes = [n for n in nodes if comp[n] == main]
        keep = set(nodes)
        edges = {e for e in edges if e[0] in keep and e[1] in keep}
    degree = dict.fromkeys(nodes, 0)
    for a, b in edges:
        degree[a] += 1
        degree[b] += 1
    bc = M.betweenness(nodes, edges)
    orphans = set(orphan_pages(files, graph))
    out = []
    for n in nodes:
        try:
            fm = parse_fm(n.read_text(errors="replace"))
        except OSError:
            fm = {}
        out.append({"id": str(n.relative_to(WIKI)), "label": n.stem, "type": page_type(n),
                    "degree": degree[n], "betweenness": round(bc[n], 6),
                    "component": size_of[comp[n]], "orphan": n in orphans,
                    "summary": (fm.get("summary_en") or "")[:200]})
    return out, sorted((str(a.relative_to(WIKI)), str(b.relative_to(WIKI))) for a, b in edges)


def gexf(nodes, edges) -> str:
    attrs = [("type", "string"), ("degree", "integer"), ("betweenness", "double"),
             ("component", "integer"), ("orphan", "boolean"), ("summary", "string")]
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<gexf xmlns="http://gexf.net/1.3" xmlns:viz="http://gexf.net/1.3/viz" version="1.3">',
             f'  <meta lastmodifieddate="{datetime.now():%Y-%m-%d}"><creator>brainless graph_export</creator></meta>',
             '  <graph defaultedgetype="undirected" mode="static">',
             '    <attributes class="node">']
    lines += [f'      <attribute id="{i}" title="{t}" type="{k}"/>' for i, (t, k) in enumerate(attrs)]
    lines.append('    </attributes>')
    lines.append('    <nodes>')
    for n in nodes:
        r, g, b = COLOURS[n["type"]]
        size = round(4 + 3 * math.sqrt(n["degree"]), 2)
        vals = "".join(f'<attvalue for="{i}" value={quoteattr(str(n[t]).lower() if k == "boolean" else str(n[t]))}/>'
                       for i, (t, k) in enumerate(attrs))
        lines.append(f'      <node id={quoteattr(n["id"])} label={quoteattr(n["label"])}>'
                     f'<attvalues>{vals}</attvalues>'
                     f'<viz:color r="{r}" g="{g}" b="{b}"/><viz:size value="{size}"/></node>')
    lines.append('    </nodes>')
    lines.append('    <edges>')
    lines += [f'      <edge id="{i}" source={quoteattr(a)} target={quoteattr(b)}/>' for i, (a, b) in enumerate(edges)]
    lines += ['    </edges>', '  </graph>', '</gexf>']
    return "\n".join(lines) + "\n"


def graphml(nodes, edges) -> str:
    keys = [("type", "string"), ("degree", "int"), ("betweenness", "double"), ("component", "int"),
            ("orphan", "boolean"), ("summary", "string"), ("label", "string")]
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">']
    lines += [f'  <key id="{k}" for="node" attr.name="{k}" attr.type="{t}"/>' for k, t in keys]
    lines.append('  <graph edgedefault="undirected">')
    for n in nodes:
        data = "".join(f'<data key="{k}">{escape(str(n[k]).lower() if t == "boolean" else str(n[k]))}</data>'
                       for k, t in keys)
        lines.append(f'    <node id={quoteattr(n["id"])}>{data}</node>')
    lines += [f'    <edge source={quoteattr(a)} target={quoteattr(b)}/>' for a, b in edges]
    lines += ['  </graph>', '</graphml>']
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--format", choices=["gexf", "graphml"], default="gexf")
    ap.add_argument("--no-summaries", action="store_true", help="hide source summaries: the concept graph")
    ap.add_argument("--main-only", action="store_true", help="keep only the largest connected component")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    nodes, edges = build(args.no_summaries, args.main_only)
    out = args.out or VAULT / "logs" / "graph" / (
        f"brainless-{datetime.now():%Y-%m-%d}{'-concepts' if args.no_summaries else ''}"
        f"{'-main' if args.main_only else ''}.{args.format}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(gexf(nodes, edges) if args.format == "gexf" else graphml(nodes, edges))
    top = sorted(nodes, key=lambda n: -n["betweenness"])[:3]
    print(f"[ok] {out}: {len(nodes)} nodes, {len(edges)} edges; "
          f"top bridges: {', '.join(n['label'] for n in top)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
