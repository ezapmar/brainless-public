#!/usr/bin/env python3
"""wiki_dedupe.py: find pages that are probably one page. Propose, never merge.

Duplicates are inevitable: two sources name one idea differently, an alias was
missing, a person appears under a nickname. The job is to catch them before
both accumulate links. Merging stays a human decision, because two pages that
look identical from their titles are often a general case and a specific one,
and a merge cannot be told apart afterwards. Reviewing a proposal takes
fifteen seconds; untangling a wrong merge does not.

Signals, most reliable first:
  alias      two pages claim the same name (frontmatter aliases, the concept
             and entity registries). Always a bug.
  title      normalised titles match or nearly match (plurals, Turkish letters,
             punctuation folded).
  clash      an entity and a project mirror share a file name. Not a
             duplicate, but [[Name]] resolves to either one, so a link can land
             on the wrong page.
  inbound    the same pages link to both (Jaccard >= 0.7 with 3+ shared).
             Concepts and ideas only: people and products that co-occur in
             the same meetings are linked together without being one thing.

The merge procedure lives in .wiki/_commands/_shared-rules.md.

Usage: python3 tools/wiki_dedupe.py
"""
import difflib
import json
import re
import sys
import unicodedata
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lint_wiki import VAULT, WIKI, all_wiki_files, link_graph, parse_fm  # noqa: E402

FOLDERS = ("concepts", "entities", "ideas", "articles", "projects")
TITLE_RATIO = 0.9
INBOUND_JACCARD = 0.7
INBOUND_FOLDERS = ("concepts", "ideas", "articles")
INBOUND_MIN_SHARED = 3
REGISTRIES = (VAULT / "_Agent-Context" / "concepts.md", VAULT / "_Agent-Context" / "entities.md")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFC", s).casefold()
    for a, b in (("ı", "i"), ("ş", "s"), ("ğ", "g"), ("ü", "u"), ("ö", "o"), ("ç", "c"), ("i̇", "i")):
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    words = [re.sub(r"(lar|ler|s)$", "", w) if len(w) > 4 else w for w in s.split()]
    return " ".join(words)


def title_of(path: Path, text: str) -> str:
    m = re.search(r"^# (.+)$", text, re.M)
    return m.group(1).strip() if m else path.stem


def aliases_of(fm: dict) -> list[str]:
    raw = fm.get("aliases", "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            return [str(a) for a in json.loads(raw)]
        except ValueError:
            raw = raw.strip("[]")
    return [a.strip().strip("'\"") for a in raw.split(",") if a.strip()]


def registry_aliases() -> dict[str, list[str]]:
    """page stem -> names the registries give it. entities.md rows start with the
    page name; concepts.md rows start with the slug."""
    out = {}
    for reg in REGISTRIES:
        try:
            text = reg.read_text()
        except OSError:
            continue
        for line in text.splitlines():
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 3 or not parts[0] or line.lstrip().startswith(("#", ">", "Row", "Format")):
                continue
            names = [parts[0]] + ([parts[1]] if reg.name == "concepts.md" else [])
            names += [a.strip() for a in parts[2].split(",") if a.strip()]
            out.setdefault(parts[0], []).extend(names)
    return out


def candidates(files):
    return [p for p in files if p.parent.name in FOLDERS or p.parent.parent.name == "projects"]


def proposals() -> list[dict]:
    files = all_wiki_files()
    pages = candidates(files)
    if not pages:
        return []
    graph = link_graph(files)
    reg = registry_aliases()
    info = {}
    for p in pages:
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        names = {p.stem, title_of(p, text), *aliases_of(parse_fm(text)), *reg.get(p.stem, [])}
        info[p] = {"title": title_of(p, text), "names": {norm(n) for n in names if norm(n)}}
    inbound = {p: set() for p in info}
    for a, b in graph["edges"]:
        if b in inbound and a != b and a.name != "INDEX.md":
            inbound[b].add(a)

    found = {}
    for a, b in combinations(sorted(info, key=str), 2):
        ia, ib = info[a], info[b]
        if a.stem == b.stem:
            found[(a, b)] = ("clash", "same file name in two folders; [[links]] are ambiguous")
            continue
        shared_names = ia["names"] & ib["names"]
        if shared_names:
            found[(a, b)] = ("alias", f"both answer to '{sorted(shared_names)[0]}'")
            continue
        ratio = difflib.SequenceMatcher(None, norm(ia["title"]), norm(ib["title"])).ratio()
        if ratio >= TITLE_RATIO:
            found[(a, b)] = ("title", f"titles {ratio:.0%} alike")
            continue
        if a.parent.name not in INBOUND_FOLDERS or b.parent.name not in INBOUND_FOLDERS:
            continue
        sa, sb = inbound[a], inbound[b]
        shared = sa & sb
        if len(shared) >= INBOUND_MIN_SHARED and len(shared) / len(sa | sb) >= INBOUND_JACCARD:
            found[(a, b)] = ("inbound", f"{len(shared)} pages link to both "
                                        f"({len(shared) / len(sa | sb):.0%} overlap)")
    order = {"alias": 0, "title": 1, "clash": 2, "inbound": 3}
    out = [{"a": str(a.relative_to(WIKI)), "b": str(b.relative_to(WIKI)), "signal": s, "why": why}
           for (a, b), (s, why) in found.items()]
    return sorted(out, key=lambda d: (order[d["signal"]], d["a"]))


def markdown(props: list[dict]) -> list[str]:
    lines = ["## Merge proposals", "",
             "_Proposals only. Nothing is merged automatically; the procedure is in "
             "`.wiki/_commands/_shared-rules.md`._", ""]
    if not props:
        return lines + ["None.", ""]
    for d in props[:40]:
        lines.append(f"- **{d['signal']}**: `{d['a']}` and `{d['b']}` ({d['why']})")
    if len(props) > 40:
        lines.append(f"- ... +{len(props) - 40}")
    return lines + [""]


def main():
    print("\n".join(markdown(proposals())))


if __name__ == "__main__":
    main()
