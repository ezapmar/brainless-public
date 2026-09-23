#!/usr/bin/env python3
"""semantic_index.py: find a page by what it means, not the words it uses.

BM25 only finds a page that shares words with the question. The owner asks in
Turkish about an English book, says "medical cover" where the memo says
"health insurance", or describes a role without its name. Those questions
miss. An embedding puts a question and a passage close together when they
mean the same thing, in either language.

Everything runs on this machine: the vault holds finance and family pages, so
no text is sent to an embedding API. fastembed runs an ONNX model locally; the
model downloads once from Hugging Face into ~/.cache/fastembed.

Each page is cut into passages (title and summary_en lead the first one, since
they are the page's own account of itself). A page scores as its best passage.
The index lives in .agents/state/semantic/<model>/ and is rebuilt
incrementally: only pages whose text changed are embedded again. Each machine
builds its own; nothing here is committed.

Optional addon. Without fastembed installed, available() is False and
wiki_search.py stays on BM25 alone.

Usage:
  python3 tools/semantic_index.py build [--model NAME]   update the index
  python3 tools/semantic_index.py query "text" [--k 10]  nearest pages
  python3 tools/semantic_index.py status                 size and age
Needs: pip install -r requirements-search.txt
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

VAULT = Path(os.environ.get("BRAINLESS_VAULT") or Path(__file__).resolve().parents[1])
WIKI = VAULT / ".wiki"
STATE = VAULT / ".agents" / "state" / "semantic"
MODEL = os.environ.get("BRAINLESS_EMBED_MODEL", "intfloat/multilingual-e5-large")
# fastembed's default cache is the system temp dir, which macOS empties: a 2 GB
# model would download again after every clean.
CACHE = os.environ.get("FASTEMBED_CACHE_PATH") or str(Path.home() / ".cache" / "fastembed")
# Hugging Face's xet transfer stalled mid-download here (2026-09-23); plain HTTPS
# resumes and finishes.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

# e5 models were trained with these prefixes; without them recall drops.
PREFIX = {"intfloat/": ("query: ", "passage: ")}
PASSAGE_CHARS = 1200
OVERLAP = 200
MAX_PASSAGES = 12  # a 40-page raw import is mostly noise past the first dozen


def available() -> bool:
    try:
        import fastembed  # noqa: F401
        import numpy  # noqa: F401
    except ImportError:
        return False
    return True


def _prefixes(model: str) -> tuple[str, str]:
    for k, v in PREFIX.items():
        if model.startswith(k):
            return v
    return ("", "")


def _dir(model: str) -> Path:
    return STATE / re.sub(r"[^A-Za-z0-9._-]+", "_", model)


_FM = re.compile(r"\A---\n(.*?)\n---\n?", re.S)


def passages(text: str, title: str) -> list[str]:
    """Title + summary_en, then the body in overlapping windows."""
    m = _FM.match(text)
    fm, body = (m.group(1), text[m.end():]) if m else ("", text)
    lead = title
    s = re.search(r"^summary_en:\s*(.+)$", fm, re.M)
    if s:
        lead += ". " + s.group(1).strip().strip('"')
    body = re.sub(r"\s+", " ", body).strip()
    out, i = [], 0
    while i < len(body) and len(out) < MAX_PASSAGES:
        out.append(body[i:i + PASSAGE_CHARS])
        i += PASSAGE_CHARS - OVERLAP
    if out:
        out[0] = lead + "\n" + out[0]
    else:
        out = [lead]
    return out


def _pages(root: Path) -> list[Path]:
    from wiki_search import _searchable
    return sorted(p for p in root.rglob("*.md") if _searchable(p))


class Index:
    def __init__(self, model: str = MODEL):
        self.model = model
        self.dir = _dir(model)
        self._emb = None
        self.vecs = None
        self.meta = {"pages": {}, "rows": []}  # rows[i] = [rel path, passage no]

    def _embedder(self):
        if self._emb is None:
            from fastembed import TextEmbedding
            self._emb = TextEmbedding(self.model, cache_dir=CACHE)
        return self._emb

    def load(self) -> bool:
        import numpy as np
        try:
            self.meta = json.loads((self.dir / "meta.json").read_text())
            self.vecs = np.load(self.dir / "vectors.npy")
        except (OSError, ValueError):
            return False
        return len(self.meta["rows"]) == len(self.vecs)

    def build(self, root: Path = WIKI, log=print) -> dict:
        import numpy as np
        self.load()
        old_pages, old_rows = self.meta.get("pages", {}), self.meta.get("rows", [])
        old_vecs = self.vecs if self.vecs is not None else np.zeros((0, 0), np.float16)
        keep = {}  # rel -> list of old row indexes, for pages whose text is unchanged
        for i, (rel, _) in enumerate(old_rows):
            keep.setdefault(rel, []).append(i)
        pages, rows, chunks, reuse = {}, [], [], []
        todo = []
        for p in _pages(root):
            rel = os.path.relpath(p, VAULT)
            try:
                text = p.read_text(errors="ignore")
            except OSError:
                continue
            h = hashlib.sha1(text.encode()).hexdigest()
            pages[rel] = h
            if old_pages.get(rel) == h and rel in keep:
                for i in keep[rel]:
                    rows.append(old_rows[i])
                    reuse.append(old_vecs[i])
            else:
                todo.append((rel, passages(text, p.stem)))
        _, ppre = _prefixes(self.model)
        for rel, ps in todo:
            for j, t in enumerate(ps):
                chunks.append(ppre + t)
                rows.append([rel, j])
        t0 = time.time()
        new = []
        if chunks:
            log(f"embedding {len(chunks)} passages from {len(todo)} pages with {self.model}")
            new = list(self._embedder().embed(chunks, batch_size=32))
        vecs = np.array(reuse + new, dtype=np.float32) if (reuse or new) else np.zeros((0, 1), np.float32)
        if len(vecs):
            vecs /= np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9
        self.vecs = vecs.astype(np.float16)
        self.meta = {"model": self.model, "built": time.strftime("%Y-%m-%dT%H:%M:%S"),
                     "pages": pages, "rows": rows}
        self.dir.mkdir(parents=True, exist_ok=True)
        np.save(self.dir / "vectors.npy", self.vecs)
        (self.dir / "meta.json").write_text(json.dumps(self.meta, ensure_ascii=False))
        return {"pages": len(pages), "passages": len(rows), "embedded": len(chunks),
                "seconds": round(time.time() - t0, 1)}

    def query(self, text: str, k: int = 50) -> list[tuple[float, str, int]]:
        """[(cosine, rel path, best passage no)] by page, best first."""
        import numpy as np
        if self.vecs is None and not self.load():
            return []
        qpre, _ = _prefixes(self.model)
        q = np.array(next(iter(self._embedder().embed([qpre + text]))), dtype=np.float32)
        q /= np.linalg.norm(q) + 1e-9
        sims = self.vecs.astype(np.float32) @ q
        best = {}
        for i in np.argsort(-sims):
            rel, j = self.meta["rows"][i]
            if rel not in best:
                best[rel] = (float(sims[i]), rel, j)
                if len(best) >= k:
                    break
        return list(best.values())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "query", "status"])
    ap.add_argument("text", nargs="?")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--k", type=int, default=10)
    args = ap.parse_args(argv)
    if not available():
        print("fastembed not installed: pip install -r requirements-search.txt")
        return 1
    idx = Index(args.model)
    if args.cmd == "build":
        res = idx.build()
        print(f"RUNLOG pages={res['pages']} passages={res['passages']} "
              f"embedded={res['embedded']} seconds={res['seconds']}")
    elif args.cmd == "status":
        if not idx.load():
            print(f"no index for {args.model}")
            return 1
        print(f"{args.model}: {len(idx.meta['pages'])} pages, "
              f"{len(idx.meta['rows'])} passages, built {idx.meta.get('built')}")
    else:
        for s, rel, j in idx.query(args.text or "", k=args.k):
            print(f"{s:.3f}  {rel}  (passage {j})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
