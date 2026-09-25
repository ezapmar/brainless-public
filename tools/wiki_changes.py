#!/usr/bin/env python3
"""wiki_changes.py: what the nightly compile changed, as a short brief.

The compile runs at night on the worker and the owner meets its result in the
morning with no account of what it did: which summaries are new, which concept
pages moved, which contradictions were raised. A loop you cannot see is a loop
you stop trusting, so the compile now leaves a brief behind.

The brief is a diff of two snapshots of .wiki/, taken before and after the
compile by nightly_compile.py. It is deterministic and LLM-free: page hashes,
wikilinks, and the Contested and Superseded bullets of concept pages.

Written to _Agent-Context/WIKI-CHANGES.md (overwritten each night). The morning
briefing reads it (AGENT-RULES, Briefing Convention).

Usage:
  python3 tools/wiki_changes.py --since REF    diff REF..working tree, print it
  python3 tools/wiki_changes.py --since REF --write
"""
import argparse
import hashlib
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from i18n import t  # noqa: E402

VAULT = Path(os.environ.get("BRAINLESS_VAULT") or Path(__file__).resolve().parents[1])
WIKI = VAULT / ".wiki"
REPORT = VAULT / "_Agent-Context" / "WIKI-CHANGES.md"

# Compiled page kinds, in the order the brief lists them. Everything else under
# .wiki/ (INDEX, reports, digests, archive, command specs) is left out: those
# files change every night by design and would drown the signal.
KINDS = ("concepts", "summaries", "entities", "projects", "ideas", "moc", "relationships")
MAX_LISTED = 8

_LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
_TITLE_RE = re.compile(r"^# (.+)$", re.M)
_FM_RE = re.compile(r"\A---\n.*?\n---\n?", re.S)


def kind_of(rel: str) -> str | None:
    parts = rel.split("/")
    return parts[0] if len(parts) > 1 and parts[0] in KINDS else None


def section_bullets(text: str, heading: str) -> list[str]:
    """Top-level '- ' bullets under one '## ' heading, first line only."""
    out, inside = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line.strip() == heading
            continue
        if inside and line.startswith("- "):
            out.append(line[2:].strip())
    return out


def page_record(text: str, rel: str) -> dict:
    body = _FM_RE.sub("", text, count=1)
    m = _TITLE_RE.search(body)
    rec = {
        "sha": hashlib.sha1(text.encode("utf-8")).hexdigest()[:12],
        "title": m.group(1).strip() if m else Path(rel).stem,
        "links": sorted({l.strip() for l in _LINK_RE.findall(body)}),
    }
    if kind_of(rel) == "concepts":
        rec["contested"] = section_bullets(body, t("compile_resources.concept_contested_heading"))
        rec["superseded"] = section_bullets(body, t("compile_resources.concept_superseded_heading"))
    return rec


def _ignored(rels: list[str]) -> set[str]:
    """Paths git ignores: guarded pages never reach the tracked brief."""
    if not rels:
        return set()
    try:
        r = subprocess.run(["git", "check-ignore", "--stdin"], cwd=VAULT, input="\n".join(
            f".wiki/{x}" for x in rels), capture_output=True, text=True, timeout=30)
        return {x[len(".wiki/"):] for x in r.stdout.splitlines()}
    except Exception:
        return set()


def snapshot(wiki: Path = WIKI) -> dict:
    """{relpath: record} for every compiled page on disk."""
    snap = {}
    for kind in KINDS:
        base = wiki / kind
        if not base.is_dir():
            continue
        for p in base.rglob("*.md"):
            rel = p.relative_to(wiki).as_posix()
            try:
                snap[rel] = page_record(p.read_text(encoding="utf-8", errors="ignore"), rel)
            except OSError:
                continue
    return snap


def snapshot_at(ref: str) -> dict:
    """The same snapshot as it stood at a git ref (for manual runs and tests)."""
    # -z: without it git quotes non-ASCII paths, and names contain spaces.
    ls = subprocess.run(["git", "ls-tree", "-r", "-z", "--name-only", ref, "--", ".wiki"],
                        cwd=VAULT, capture_output=True, text=True, check=True).stdout.split("\0")
    rels = [x[len(".wiki/"):] for x in ls if x.endswith(".md") and kind_of(x[len(".wiki/"):])]
    if not rels:
        return {}
    batch = "".join(f"{ref}:.wiki/{r}\n" for r in rels).encode()
    out = subprocess.run(["git", "cat-file", "--batch"], cwd=VAULT, input=batch,
                         capture_output=True, check=True).stdout
    snap, pos = {}, 0
    for rel in rels:
        nl = out.index(b"\n", pos)
        size = int(out[pos:nl].split()[2])
        text = out[nl + 1:nl + 1 + size].decode("utf-8", errors="ignore")
        pos = nl + 1 + size + 1
        snap[rel] = page_record(text, rel)
    return snap


def diff(before: dict, after: dict) -> dict:
    """Pages added, updated and removed per kind; links added; new flags."""
    res = {"added": {}, "updated": {}, "removed": {}, "links": 0, "linked_pages": 0,
           "contested": [], "superseded": []}
    for rel in sorted(set(before) | set(after)):
        k = kind_of(rel)
        old, new = before.get(rel), after.get(rel)
        if old and not new:
            res["removed"].setdefault(k, []).append(old["title"])
            continue
        if new and not old:
            res["added"].setdefault(k, []).append(new["title"])
        elif old["sha"] != new["sha"]:
            res["updated"].setdefault(k, []).append(new["title"])
        else:
            continue
        gained = set(new["links"]) - set(old["links"] if old else [])
        if gained:
            res["links"] += len(gained)
            res["linked_pages"] += 1
        for key in ("contested", "superseded"):
            seen = set((old or {}).get(key, []))
            for b in new.get(key, []):
                if b not in seen and not b.startswith("("):
                    res[key].append((new["title"], b))
    return res


def is_empty(d: dict) -> bool:
    return not (d["added"] or d["updated"] or d["removed"] or d["contested"] or d["superseded"])


def _names(titles: list[str]) -> str:
    shown = ", ".join(titles[:MAX_LISTED])
    extra = len(titles) - MAX_LISTED
    return shown + (t("wiki_changes.more", n=extra) if extra > 0 else "")


def _clip(s: str, n: int = 160) -> str:
    s = re.sub(r"\s+", " ", s)
    return s if len(s) <= n else s[:n - 3].rstrip() + "..."


def markdown(d: dict, when: datetime | None = None) -> str:
    when = when or datetime.now()
    lines = [t("wiki_changes.title"), "",
             t("wiki_changes.updated_line", time=when.strftime("%Y-%m-%d %H:%M")), ""]
    if is_empty(d):
        lines += [t("wiki_changes.nothing"), ""]
        return "\n".join(lines)
    lines.append(t("wiki_changes.changed_heading"))
    for k in KINDS:
        label = t(f"wiki_changes.kind_{k}")
        for key in ("added", "updated", "removed"):
            titles = d[key].get(k)
            if titles:
                lines.append(t(f"wiki_changes.{key}", kind=label, n=len(titles), names=_names(titles)))
    lines += ["", t("wiki_changes.linked_heading"),
              t("wiki_changes.links_line", links=d["links"], pages=d["linked_pages"]), ""]
    lines.append(t("wiki_changes.flagged_heading"))
    if not (d["contested"] or d["superseded"]):
        lines.append(t("wiki_changes.no_flags"))
    for key in ("contested", "superseded"):
        for title, bullet in d[key][:MAX_LISTED]:
            lines.append(t(f"wiki_changes.flag_{key}", page=title, text=_clip(bullet)))
    lines.append("")
    return "\n".join(lines)


def tracked_diff(before: dict, after: dict) -> dict:
    """diff() without the pages git ignores."""
    hidden = _ignored(sorted(set(before) | set(after)))
    return diff({k: v for k, v in before.items() if k not in hidden},
                {k: v for k, v in after.items() if k not in hidden})


def write(before: dict, after: dict) -> dict:
    """Diff the tracked pages and write the brief; returns the diff."""
    d = tracked_diff(before, after)
    REPORT.write_text(markdown(d), encoding="utf-8")
    return d


def count(d: dict) -> int:
    return sum(len(v) for key in ("added", "updated", "removed") for v in d[key].values())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--since", required=True, help="git ref to diff the working tree against")
    ap.add_argument("--write", action="store_true", help=f"write {REPORT.name}")
    args = ap.parse_args(argv)
    before, after = snapshot_at(args.since), snapshot()
    if args.write:
        d = write(before, after)
        print(f"{REPORT.relative_to(VAULT)}: {count(d)} page(s) changed")
    else:
        print(markdown(tracked_diff(before, after)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
