#!/usr/bin/env python3
"""retrieval_eval.py: does asking the vault still find the right page?

Search quality degrades without an error. A compile change rewrites summaries
in a different shape, a concept page starts outranking its sources, a prune
rule archives the page a question depended on. Every answer still looks
fluent. The only way to see it is a fixed set of questions with known answer
pages, scored the same way every night.

The golden set lives in _Agent-Context/retrieval-golden.json (private: it names
real pages). Each item: {"q": question as the owner would ask it, "expect":
[page stems, any one counts], "why": what the question tests, "kind": name |
paraphrase | crosslang | claim}. The kind splits the score, because a search
change usually helps one kind and costs another, and the average hides that.
A question is written in the owner's words, not the page's, because that is
the gap search has to cross. Never quote a golden question in a vault page:
the page then outranks the answer and the eval measures the quote.

Scores: hit@1, hit@5 (an expected page in the top 5), MRR@10. The previous run
is kept in .agents/state/retrieval_eval.json so a change names the questions
that moved, not just the average.

Usage:
  python3 tools/retrieval_eval.py            # table, misses, change since last run
  python3 tools/retrieval_eval.py --json     # machine output
  python3 tools/retrieval_eval.py --min-hit5 70   # exit 1 below the floor (for CI)
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wiki_search import VAULT, mode, search  # noqa: E402

GOLDEN = VAULT / "_Agent-Context" / "retrieval-golden.json"
LAST = VAULT / ".agents" / "state" / "retrieval_eval.json"
K = 10


def load_golden(path: Path = GOLDEN) -> list[dict]:
    try:
        items = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    return [i for i in items if isinstance(i, dict) and i.get("q") and i.get("expect")]


def rank_of(expect: list[str], stems: list[str]) -> int | None:
    """1-based rank of the first expected page, or None."""
    want = set(expect)
    for i, s in enumerate(stems, 1):
        if s in want:
            return i
    return None


def evaluate(items: list[dict] | None = None, k: int = K) -> dict:
    items = load_golden() if items is None else items
    rows = []
    for it in items:
        stems = [Path(d["path"]).stem for _, d in search(it["q"], k=k)]
        r = rank_of(it["expect"], stems)
        rows.append({"q": it["q"], "rank": r, "top": stems[:3], "expect": it["expect"],
                     "kind": it.get("kind", "other")})
    kinds = {}
    for kind in sorted({r["kind"] for r in rows}):
        kinds[kind] = scores([r for r in rows if r["kind"] == kind])
    return {"n": len(rows), "mode": mode(), **scores(rows), "kinds": kinds, "rows": rows}


def scores(rows: list[dict]) -> dict:
    n = len(rows) or 1
    return {
        "hit1": round(100 * sum(1 for r in rows if r["rank"] == 1) / n),
        "hit5": round(100 * sum(1 for r in rows if r["rank"] and r["rank"] <= 5) / n),
        "mrr": round(100 * sum(1 / r["rank"] for r in rows if r["rank"]) / n),
    }


def changes(prev: dict, cur: dict) -> list[str]:
    before = {r["q"]: r["rank"] for r in prev.get("rows", [])}
    out = []
    for r in cur["rows"]:
        if r["q"] not in before or before[r["q"]] == r["rank"]:
            continue
        b, a = before[r["q"]], r["rank"]
        worse = (a is None) or (b is not None and a > b)
        out.append(f"{'worse' if worse else 'better'}: {r['q'][:60]} ({b or '-'} -> {a or '-'})")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--min-hit5", type=int, default=None)
    args = ap.parse_args(argv)
    res = evaluate()
    if not res["n"]:
        print(f"no golden set at {GOLDEN.relative_to(VAULT)}")
        return 0
    try:
        prev = json.loads(LAST.read_text())
    except (OSError, ValueError):
        prev = {}
    moved = changes(prev, res)
    LAST.parent.mkdir(parents=True, exist_ok=True)
    LAST.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    if args.json:
        print(json.dumps({**res, "changes": moved}, ensure_ascii=False))
    else:
        print(f"{res['n']} questions ({res['mode']}): hit@1 {res['hit1']}%, hit@5 {res['hit5']}%, MRR@{K} {res['mrr']}")
        if prev:
            print(f"last run ({prev.get('mode', 'bm25')}): hit@1 {prev.get('hit1')}%, hit@5 {prev.get('hit5')}%, MRR {prev.get('mrr')}")
        for kind, sc in res["kinds"].items():
            cnt = sum(1 for r in res["rows"] if r["kind"] == kind)
            print(f"  {kind:<10} {cnt:>2}q  hit@1 {sc['hit1']:>3}%  hit@5 {sc['hit5']:>3}%  MRR {sc['mrr']:>3}")
        misses = [r for r in res["rows"] if not r["rank"] or r["rank"] > 5]
        if misses:
            print("\nNot in the top 5:")
            for r in misses:
                print(f"  {r['rank'] or '-':>2}  {r['q'][:70]}\n      want {r['expect'][0]}, got {', '.join(r['top'])}")
        if moved:
            print("\nChanged since last run:")
            print("\n".join(f"  {m}" for m in moved))
    print(f"RUNLOG mode={res['mode']} hit1={res['hit1']} hit5={res['hit5']} mrr={res['mrr']}")
    if args.min_hit5 is not None and res["hit5"] < args.min_hit5:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
