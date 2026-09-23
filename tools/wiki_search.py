#!/usr/bin/env python3
"""
wiki_search.py: small CLI search over wiki/.

BM25 over the wiki, fused with local embeddings when tools/semantic_index.py
has built an index (hybrid: reciprocal rank fusion of the two rankings). BM25
finds the page that shares the question's words; the embedding finds the page
that means the same thing in other words or the other language. Without the
addon or an index, search is BM25 alone, as before.
Designed to be called by slash commands and by Claude Code as a tool.

BRAINLESS_SEARCH=bm25|hybrid|rerank picks the mode (default: hybrid when an
index exists). rerank reorders the fused top 30 with a local cross-encoder.

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
from collections import Counter
from pathlib import Path

# Portable vault root: env override, else the repo that contains this script.
VAULT = Path(os.environ.get("BRAINLESS_VAULT") or Path(__file__).resolve().parents[1])

STOP = set("the a an and or of to in for on at by is are was were be with this that as it if then so".split())


# Turkish letters are folded to ASCII on both sides: people type "maas" for
# "maaş" and "tasinma" for "taşınma", and models write aliases either way.
_FOLD = str.maketrans("çğıöşüâîû", "cgiosuaiu")


def tokenize(s: str):
    s = s.replace("İ", "i").replace("I", "ı").lower().translate(_FOLD)
    return [t for t in re.findall(r"[a-z0-9]+", s) if t not in STOP and len(t) > 1]


# Pages tools/wiki_prune.py has archived stay on disk for the record but leave
# the search: a result nobody linked to in two months is the pile talking.
ARCHIVE_DIR = "_archive"
# INDEX.md and the topic indexes name every page, so they would outrank the pages
# themselves. An agent reads the index directly; search returns pages.
INDEX_DIR = "_index"


# Generated reports list pages by name and would outrank them.
REPORTS = {"INDEX.md", "_lint-report.md", "_link-suggestions.md"}


def _searchable(p: Path) -> bool:
    return ARCHIVE_DIR not in p.parts and INDEX_DIR not in p.parts and p.name not in REPORTS


def walk(root: Path):
    return [str(p) for p in root.rglob("*.md") if _searchable(p)]


def rg_candidates(query: str, root: Path):
    try:
        out = subprocess.run(
            ["rg", "--no-heading", "--line-number", "--ignore-case",
             "--max-count", "5", "--glob", f"!{ARCHIVE_DIR}/**", "--glob", f"!{INDEX_DIR}/**",
             *[a for r in REPORTS for a in ("--glob", f"!{r}")], query, str(root)],
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
        return walk(root)


# A concept page is the compiled answer; its sources are the evidence behind it.
# Nudge it above the summaries it was built from, so a query lands on the page
# that already reconciles them and follows links outward from there.
CONCEPT_BOOST = 1.3


def boost(path) -> float:
    return CONCEPT_BOOST if f"{os.sep}concepts{os.sep}" in str(path) else 1.0


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
        scores.append((s * boost(d["path"]), d))
    scores.sort(key=lambda x: -x[0])
    return scores


def _fold(text: str) -> str:
    """tokenize()'s folding, one character for one, so an index in the folded
    text is the same index in the original."""
    return text.replace("İ", "i").replace("I", "ı").lower().translate(_FOLD)


def snippet(text: str, query_tokens, width=200):
    low = _fold(text) if len(_fold(text)) == len(text) else text.lower()
    for q in query_tokens:
        i = low.find(q)
        if i >= 0:
            start = max(0, i - width // 2)
            return text[start:start + width].replace("\n", " ")
    return text[:width].replace("\n", " ")


_TIME = re.compile(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]")


def passage(d: dict, query: str) -> dict:
    """Where in the page the answer is: the passage the embedding matched, or
    the one holding most query words, with the line it starts on and the last
    [mm:ss] marker before it, so a claim can be checked at its source.
    The passages are semantic_index's, cut the same way it cut them."""
    import semantic_index
    stem = Path(d["path"]).stem
    ps = semantic_index.passages(d["text"], stem)
    qt = set(tokenize(query))
    j = d.get("passage_no")
    if j is None or j >= len(ps):
        def overlap(t):
            toks = tokenize(t)
            return (len(qt & set(toks)), sum(1 for x in toks if x in qt))
        j = max(range(len(ps)), key=lambda i: overlap(ps[i]))
    text = ps[j]
    body = text.split("\n", 1)[1] if j == 0 and "\n" in text else text
    snip = snippet(body, list(qt)) if body.strip() else snippet(d["text"], list(qt))
    out = {"passage_no": j, "passage": body.strip(), "snippet": snip, "line": None, "at": None}
    # Find the passage in the page, then the first query word inside it: the
    # line and the [mm:ss] marker are those of the match, not of the passage.
    text_ = d["text"]
    words = body.split()[:8]
    m = re.search(r"\s+".join(re.escape(w) for w in words), text_) if words else None
    if m:
        pos, end = m.start(), m.start() + len(body) + 200
        folded = _fold(text_)
        if len(folded) == len(text_):
            hits = [i for i in (folded.find(q, pos, end) for q in qt) if i >= 0]
            pos = min(hits) if hits else pos
        out["line"] = text_.count("\n", 0, pos) + 1
        marks = _TIME.findall(text_[:pos])
        out["at"] = marks[-1] if marks else None
    return out


def _load(paths):
    docs = []
    for p in paths:
        try:
            txt = Path(p).read_text(errors="ignore")
            docs.append({"path": p, "text": txt, "tokens": tokenize(txt)})
        except Exception:
            pass
    return docs


# Reciprocal rank fusion constant. 60 is the value from the original paper and
# the one everyone keeps: it flattens the head enough that a page ranked well by
# both lists beats a page ranked first by one.
RRF_K = 60
# The vector list's vote relative to BM25's. Below 1, the embedding breaks ties
# and rescues pages BM25 missed, while a name BM25 found exactly stays on top.
VECTOR_WEIGHT = float(os.environ.get("BRAINLESS_VECTOR_WEIGHT", "1.0"))
RERANK_TOP = 30
_INDEX = None


def _semantic():
    """The loaded semantic index, or None when the addon or the index is absent."""
    global _INDEX
    if _INDEX is None:
        _INDEX = False
        try:
            import semantic_index
            # The module fixes its vault at import; a test that points
            # BRAINLESS_VAULT elsewhere must not get the real vault's index.
            if semantic_index.available() and semantic_index.VAULT.resolve() == VAULT.resolve():
                idx = semantic_index.Index()
                if idx.load():
                    _INDEX = idx
        except Exception:
            pass
    return _INDEX or None


def mode() -> str:
    m = os.environ.get("BRAINLESS_SEARCH", "").strip().lower()
    if m == "bm25":
        return m
    if _semantic() is None:
        return "bm25"
    return m if m in ("hybrid", "rerank") else "hybrid"


def search(query: str, k: int = 10, root: Path | None = None):
    """Ranked [(score, doc)] for a query; doc has path, text, tokens. The one
    entry point shared by the CLI and tools/retrieval_eval.py, so the eval
    measures exactly what an agent gets."""
    root = root or VAULT / ".wiki"
    m = mode() if root.resolve() == (VAULT / ".wiki").resolve() else "bm25"
    if m == "bm25":
        cand = rg_candidates(query, root) or walk(root)
        return bm25(tokenize(query), _load(cand))[:k]
    # Hybrid ranks every page: the rg prefilter keeps only pages containing the
    # literal phrase, which throws away exactly the pages the embedding is for.
    docs = _load(walk(root))
    by_path = {os.path.relpath(d["path"], VAULT): d for d in docs}
    # A page with no query word scores 0 in BM25 but still has a rank; only
    # real matches may vote in the fusion.
    lexical = [os.path.relpath(d["path"], VAULT) for sc, d in bm25(tokenize(query), docs)[:100] if sc > 0]
    idx = _semantic()
    hits = idx.query(query, k=100)
    passage_of = {rel: j for _, rel, j in hits}
    fused = {}
    for weight, ranking in ((1.0, lexical), (VECTOR_WEIGHT, [rel for _, rel, _ in hits])):
        for r, rel in enumerate(ranking, 1):
            if rel in by_path:
                fused[rel] = fused.get(rel, 0.0) + weight / (RRF_K + r)
    # No concept boost here: BM25 already applied it to its own list, and on
    # the flat fused scores a second 1.3x lifts every concept page to the top.
    ranked = sorted(fused.items(), key=lambda x: -x[1])
    if m == "rerank":
        ranked = _rerank(query, ranked[:RERANK_TOP], by_path, passage_of) + ranked[RERANK_TOP:]
    out = []
    for rel, sc in ranked[:k]:
        d = by_path[rel]
        if rel in passage_of:
            d["passage_no"] = passage_of[rel]
        out.append((round(sc, 4), d))
    return out


_CROSS = None
RERANK_MODEL = os.environ.get("BRAINLESS_RERANK_MODEL", "jinaai/jina-reranker-v2-base-multilingual")


def _rerank(query, ranked, by_path, passage_of):
    """Reorder by a cross-encoder reading the question next to each page's best
    passage. Slower than the fused score, and more exact."""
    global _CROSS
    import semantic_index
    from fastembed.rerank.cross_encoder import TextCrossEncoder
    if _CROSS is None:
        _CROSS = TextCrossEncoder(RERANK_MODEL, cache_dir=semantic_index.CACHE)
    texts = []
    for rel, _ in ranked:
        d = by_path[rel]
        ps = semantic_index.passages(d["text"], Path(d["path"]).stem)
        texts.append(ps[min(passage_of.get(rel, 0), len(ps) - 1)])
    scores = list(_CROSS.rerank(query, texts))
    return sorted(((rel, float(s)) for (rel, _), s in zip(ranked, scores)), key=lambda x: -x[1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--root", default=".wiki")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    ranked = search(args.query, k=args.k, root=VAULT / args.root)
    results = []
    for score, d in ranked:
        rel = os.path.relpath(d["path"], VAULT)
        results.append({"path": rel, "score": round(score, 4), **passage(d, args.query)})

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for r in results:
            where = f":{r['line']}" if r["line"] else ""
            at = f" [{r['at']}]" if r["at"] else ""
            print(f"{r['score']:8.4f}  {r['path']}{where}{at}")
            print(f"        {r['snippet']}")


if __name__ == "__main__":
    main()
