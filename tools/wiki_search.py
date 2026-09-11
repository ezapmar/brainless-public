#!/usr/bin/env python3
"""
wiki_search.py: small CLI search over wiki/.

Uses ripgrep + a naive BM25 reranker on top.
Designed to be called by slash commands and by Claude Code as a tool.

Usage:
  python3 tools/wiki_search.py "query string"
  python3 tools/wiki_search.py "query" --k 10 --root .wiki
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

# Portable vault root: env override, else the repo that contains this script.
VAULT = Path(os.environ.get("BRAINLESS_VAULT") or Path(__file__).resolve().parents[1])

STOP = set("the a an and or of to in for on at by is are was were be with this that as it if then so".split())


def tokenize(s: str):
    return [t for t in re.findall(r"[A-Za-zÇĞİÖŞÜçğıöşü0-9]+", s.lower()) if t not in STOP and len(t) > 1]


def rg_candidates(query: str, root: Path):
    try:
        out = subprocess.run(
            ["rg", "--no-heading", "--line-number", "--ignore-case",
             "--max-count", "5", query, str(root)],
            capture_output=True, text=True, timeout=15,
        )
        files = set()
        for line in out.stdout.splitlines():
            parts = line.split(":", 2)
            if len(parts) >= 1:
                files.add(parts[0])
        return list(files)
    except FileNotFoundError:
        # fallback: walk
        return [str(p) for p in root.rglob("*.md")]


def bm25(query_tokens, docs, k1=1.5, b=0.75):
    N = len(docs)
    if N == 0:
        return []
    avgdl = sum(len(d["tokens"]) for d in docs) / N
    df = Counter()
    for d in docs:
        for t in set(d["tokens"]):
            df[t] += 1
    scores = []
    for d in docs:
        tf = Counter(d["tokens"])
        s = 0.0
        for q in query_tokens:
            if q not in tf:
                continue
            idf = math.log((N - df[q] + 0.5) / (df[q] + 0.5) + 1)
            denom = tf[q] + k1 * (1 - b + b * len(d["tokens"]) / max(avgdl, 1))
            s += idf * (tf[q] * (k1 + 1)) / max(denom, 1e-6)
        scores.append((s, d))
    scores.sort(key=lambda x: -x[0])
    return scores


def snippet(text: str, query_tokens, width=200):
    low = text.lower()
    for q in query_tokens:
        i = low.find(q)
        if i >= 0:
            start = max(0, i - width // 2)
            return text[start:start + width].replace("\n", " ")
    return text[:width].replace("\n", " ")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--root", default=".wiki")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = VAULT / args.root
    qtoks = tokenize(args.query)
    cand = rg_candidates(args.query, root)
    if not cand:
        cand = [str(p) for p in root.rglob("*.md")]

    docs = []
    for p in cand:
        try:
            txt = Path(p).read_text(errors="ignore")
            docs.append({"path": p, "text": txt, "tokens": tokenize(txt)})
        except Exception:
            pass

    ranked = bm25(qtoks, docs)[: args.k]
    results = []
    for score, d in ranked:
        rel = os.path.relpath(d["path"], VAULT)
        results.append({
            "path": rel,
            "score": round(score, 3),
            "snippet": snippet(d["text"], qtoks),
        })

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for r in results:
            print(f"{r['score']:6.2f}  {r['path']}")
            print(f"        {r['snippet']}")


if __name__ == "__main__":
    main()
