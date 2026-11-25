#!/usr/bin/env python3
"""vault_graph.py: connectivity, modularity and privacy audit for the Obsidian-visible vault.

tools/lint_wiki.py and tools/wiki_metrics.py only inspect .wiki/, which Obsidian hides
because its name starts with a dot. As a result, .wiki/ can report zero orphans while
hundreds of human-owned notes under Work/, Personal/, Library/, Writings/, Inbox/,
Archive/ and Daily Briefings/ sit completely disconnected in Obsidian Graph View.

This tool audits the Obsidian-visible graph directly (read-only over human areas) and
writes a gitignored report to .wiki/_vault-graph-report.md with a compact JSON stamp
for tools/health_check.py.

Four invariants are checked:
  1. orphans            visible .md notes with 0 inbound and 0 outbound links (target: 0)
  2. main_share         fraction of visible notes in the largest connected component (target >= 99%)
  3. gitignore_leaks    tracked files explicitly linking to a gitignored path (target: 0, RED > 0)
  4. cross_unexpected   Work <-> Personal edges outside the intentional cross-effect allowlist

Custom Work <-> Personal bridges live in _Agent-Context/PROFILE.md, either as
frontmatter `cross_domain_allow: Personal/Relocation/ -> Work/Expansion/, Personal/Notes/ -> *`
or as bullet lines under `## Cross-domain allowlist`.

Usage:
  python3 tools/vault_graph.py            # write .wiki/_vault-graph-report.md
  python3 tools/vault_graph.py --json     # print JSON summary, write nothing
  python3 tools/vault_graph.py --no-write # print Markdown summary, write nothing
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from frontmatter import parse as parse_frontmatter
from fsutil import atomic_write
from paths import vault_root

VAULT = Path(vault_root())
REPORT = VAULT / ".wiki" / "_vault-graph-report.md"

SKIP_DIRS = {
    ".git", ".obsidian", ".tmp.driveupload", ".tmp.drivedownload", ".venv",
    ".pytest_cache", ".ruff_cache", "__pycache__", "_Backup", "node_modules",
    ".claude", ".agents", ".gemini", ".wiki",
}
DEFAULT_EXCLUDED = {
    "tools", "logs", "docs", "_Backup", "_Agent-Context",
    "_Templates", "_attachments", "images", "raw",
}

LINK_RE = re.compile(r"\[\[([^\]\|#]+)(?:#[^\]\|]+)?(?:\|[^\]]+)?\]\]")
MD_LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
FM_ALIAS_RE = re.compile(r"^aliases:\s*(.+)$", re.M)

# Thresholds for verdicts()
ORPHAN_WARN = 1
ORPHAN_RED = 11
MAIN_SHARE_WARN = 0.99
MAIN_SHARE_RED = 0.95

# Generic intentional Work <-> Personal bridges (prefixes on the Personal side paired with
# allowed Work prefixes; "*" allows any Work target for personal overview pages).
# Deployment-specific bridges belong in _Agent-Context/PROFILE.md (cross_domain_allow).
DEFAULT_ALLOWED_CROSS_DOMAIN: tuple[tuple[str, str], ...] = (
    ("Personal/Career.md", "*"),
    ("Personal/Finance.md", "*"),
    ("Personal/Health.md", "*"),
    ("Personal/Relationships.md", "*"),
    ("Personal/Official Docs/", "*"),
)
ALLOWED_CROSS_DOMAIN = DEFAULT_ALLOWED_CROSS_DOMAIN


def _parse_bridge_pair(raw: str) -> tuple[str, str] | None:
    parts = re.split(r"\s*(?:<->|->)\s*", raw.strip().lstrip("-* ").strip(), maxsplit=1)
    if len(parts) != 2:
        return None
    left, right = parts[0].strip().strip("`'\""), parts[1].strip().strip("`'\"")
    if not left or not right:
        return None
    if right.startswith("Personal/") and not left.startswith("Personal/"):
        left, right = right, left
    return (left, right)


def load_cross_domain_allowlist(vault: Path) -> tuple[tuple[str, str], ...]:
    """Combine generic defaults with optional cross_domain_allow entries from PROFILE.md."""
    pairs: list[tuple[str, str]] = list(DEFAULT_ALLOWED_CROSS_DOMAIN)
    profile = vault / "_Agent-Context" / "PROFILE.md"
    if not profile.is_file():
        return tuple(pairs)
    try:
        text = profile.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return tuple(pairs)
    fm = parse_frontmatter(text)
    raw_fm = (fm.get("cross_domain_allow") or "").strip()
    if raw_fm:
        for item in re.split(r"[,;\n]", raw_fm):
            pair = _parse_bridge_pair(item)
            if pair and pair not in pairs:
                pairs.append(pair)
    m = re.search(r"^## Cross-domain allowlist\s*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    if m:
        for line in m.group(1).splitlines():
            pair = _parse_bridge_pair(line)
            if pair and pair not in pairs:
                pairs.append(pair)
    return tuple(pairs)


def graph_exclusions(vault: Path) -> set[str]:
    """Read top-level '-path:X/' exclusions from .obsidian/graph.json, with a safe default."""
    cfg = vault / ".obsidian" / "graph.json"
    if not cfg.is_file():
        return set(DEFAULT_EXCLUDED)
    try:
        data = json.loads(cfg.read_text(encoding="utf-8", errors="replace"))
        search = str(data.get("search") or "")
        found = {m.group(1).strip("/") for m in re.finditer(r"-path:([^\s/]+)/?", search)}
        return found or set(DEFAULT_EXCLUDED)
    except (OSError, ValueError):
        return set(DEFAULT_EXCLUDED)


def visible_markdown_files(vault: Path) -> list[Path]:
    """Every Obsidian-visible .md file, respecting dot-dir ignores and graph.json exclusions."""
    excluded = graph_exclusions(vault)
    out = []
    for root, dirs, files in os.walk(vault):
        rel_parts = Path(root).relative_to(vault).parts
        if any(p in SKIP_DIRS or p.startswith(".") for p in rel_parts):
            dirs[:] = []
            continue
        if rel_parts and rel_parts[0] in excluded:
            dirs[:] = []
            continue
        dirs[:] = sorted(
            d for d in dirs
            if d not in SKIP_DIRS and not d.startswith(".")
            and not (not rel_parts and d in excluded)
        )
        for name in sorted(files):
            if name.endswith(".md"):
                out.append(Path(root) / name)
    return out


def _build_indexes(files: list[Path], vault: Path):
    by_rel: dict[str, Path] = {}
    by_stem: dict[str, list[Path]] = defaultdict(list)
    by_alias_sets: dict[str, set[Path]] = defaultdict(set)
    for p in files:
        rel = p.relative_to(vault).as_posix()
        by_rel[rel.lower()] = p
        by_rel[rel[:-3].lower()] = p
        by_stem[p.stem.lower()].append(p)
        if p.name == "notes.md":
            by_alias_sets[p.parent.name.lower()].add(p)
        try:
            head = p.read_text(encoding="utf-8", errors="ignore")[:1500]
            m = FM_ALIAS_RE.search(head)
            if m:
                for alias in re.findall(r'["\']([^"\']+)["\']', m.group(1)):
                    by_alias_sets[alias.lower()].add(p)
        except OSError:
            pass
    by_alias = {k: sorted(v) for k, v in by_alias_sets.items()}
    return by_rel, by_stem, by_alias


def _pick_closest(src: Path, candidates: list[Path], vault: Path) -> list[Path]:
    """Disambiguate multi-hit bare stems without broadcasting across unrelated folders."""
    if len(candidates) <= 1:
        return candidates
    src_parts = src.relative_to(vault).parts
    same_dir = [c for c in candidates if c.parent == src.parent]
    if len(same_dir) == 1:
        return same_dir
    if len(src_parts) >= 2:
        same_proj = [c for c in candidates if c.relative_to(vault).parts[:2] == src_parts[:2]]
        if len(same_proj) == 1:
            return same_proj
    same_top = [c for c in candidates if c.relative_to(vault).parts[0] == src_parts[0]]
    if len(same_top) == 1:
        return same_top
    return []


def _resolve_wiki(src: Path, target: str, by_rel, by_stem, by_alias, vault: Path) -> list[Path]:
    t = target.strip().lower()
    if not t:
        return []
    if t in by_rel:
        return [by_rel[t]]
    if t + ".md" in by_rel:
        return [by_rel[t + ".md"]]
    stem = Path(t).stem.lower()
    if stem in by_stem:
        return _pick_closest(src, by_stem[stem], vault)
    if t in by_alias:
        return _pick_closest(src, by_alias[t], vault)
    if stem in by_alias:
        return _pick_closest(src, by_alias[stem], vault)
    return []


def _is_allowed_cross(a_rel: str, b_rel: str, allowlist: tuple[tuple[str, str], ...]) -> bool:
    pers, work = (a_rel, b_rel) if a_rel.startswith("Personal/") else (b_rel, a_rel)
    for p_pref, w_pref in allowlist:
        p_ok = pers == p_pref or pers.startswith(p_pref)
        w_ok = w_pref == "*" or work == w_pref or work.startswith(w_pref)
        if p_ok and w_ok:
            return True
    return False


def _gitignored_paths(rels: list[str], vault: Path) -> set[str]:
    if not rels or not (vault / ".git").exists():
        return set()
    try:
        proc = subprocess.run(
            ["git", "check-ignore", "--no-index", "-z", "--stdin"],
            cwd=vault,
            input="\0".join(rels),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=30,
        )
        return {x for x in proc.stdout.split("\0") if x}
    except (OSError, subprocess.SubprocessError):
        return set()


def _components(nodes: list[Path], edges: set[tuple[Path, Path]]) -> list[list[Path]]:
    parent = {n: n for n in nodes}

    def find(x: Path) -> Path:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    groups: dict[Path, list[Path]] = defaultdict(list)
    for n in nodes:
        groups[find(n)].append(n)
    return sorted(groups.values(), key=len, reverse=True)


def compute(
    vault: Path | None = None,
    *,
    check_gitignore: bool = True,
    allowed_cross_domain: tuple[tuple[str, str], ...] | None = None,
) -> dict:
    """Compute graph connectivity, modularity and privacy-leak metrics."""
    vault = Path(vault).resolve() if vault is not None else VAULT.resolve()
    files = visible_markdown_files(vault)
    vis_set = set(files)
    by_rel, by_stem, by_alias = _build_indexes(files, vault)
    allowlist = (
        allowed_cross_domain
        if allowed_cross_domain is not None
        else load_cross_domain_allowlist(vault)
    )

    in_deg: Counter[Path] = Counter()
    out_deg: Counter[Path] = Counter()
    edges: set[tuple[Path, Path]] = set()
    explicit_links: list[tuple[str, str]] = []
    cross_allowed: set[tuple[str, str]] = set()
    cross_unexpected: set[tuple[str, str]] = set()

    for p in files:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        p_rel = p.relative_to(vault).as_posix()
        p_top = p.relative_to(vault).parts[0] if len(p.relative_to(vault).parts) > 1 else "<root>"
        targets: list[tuple[Path, bool]] = []

        for m in LINK_RE.finditer(text):
            raw_t = m.group(1).strip()
            explicit = "/" in raw_t
            for hit in _resolve_wiki(p, raw_t, by_rel, by_stem, by_alias, vault):
                targets.append((hit, explicit))

        for m in MD_LINK_RE.finditer(text):
            href = m.group(1).strip()
            if href.startswith(("http://", "https://", "mailto:", "#")):
                continue
            clean_href = urllib.parse.unquote(href.split("#")[0])
            if not clean_href:
                continue
            try:
                cand = (p.parent / clean_href).resolve()
                rel_key = cand.relative_to(vault).as_posix().lower()
            except (ValueError, OSError):
                continue
            if rel_key in by_rel:
                targets.append((by_rel[rel_key], True))

        for hit, is_explicit in targets:
            if hit == p or hit not in vis_set:
                continue
            out_deg[p] += 1
            in_deg[hit] += 1
            a, b = (p, hit) if str(p) < str(hit) else (hit, p)
            edges.add((a, b))
            h_rel = hit.relative_to(vault).as_posix()
            if is_explicit:
                explicit_links.append((p_rel, h_rel))
            h_top = hit.relative_to(vault).parts[0] if len(hit.relative_to(vault).parts) > 1 else "<root>"
            if {p_top, h_top} == {"Work", "Personal"}:
                pair = tuple(sorted((p_rel, h_rel)))
                if _is_allowed_cross(p_rel, h_rel, allowlist):
                    cross_allowed.add(pair)
                else:
                    cross_unexpected.add(pair)

    orphans = sorted(
        p.relative_to(vault).as_posix()
        for p in files
        if in_deg[p] == 0 and out_deg[p] == 0
    )
    comps = _components(files, edges)
    n = len(files)
    main_size = len(comps[0]) if comps else 0
    main_share = round(main_size / n, 4) if n else 1.0
    avg_degree = round(2 * len(edges) / n, 2) if n else 0.0
    islands = [
        {"size": len(c), "sample": [p.relative_to(vault).as_posix() for p in sorted(c)[:3]]}
        for c in comps[1:]
    ]

    leaks: list[tuple[str, str]] = []
    if check_gitignore and explicit_links:
        all_rels = sorted({r for pair in explicit_links for r in pair})
        ignored = _gitignored_paths(all_rels, vault)
        seen_leaks = set()
        for src_rel, tgt_rel in explicit_links:
            if tgt_rel in ignored and src_rel not in ignored:
                key = (src_rel, tgt_rel)
                if key not in seen_leaks:
                    seen_leaks.add(key)
                    leaks.append(key)
        leaks.sort()

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "nodes": n,
        "edges": len(edges),
        "avg_degree": avg_degree,
        "orphans": orphans,
        "orphan_count": len(orphans),
        "components": len(comps),
        "main_share": main_share,
        "islands": islands,
        "cross_allowed_count": len(cross_allowed),
        "cross_unexpected": sorted(cross_unexpected),
        "cross_unexpected_count": len(cross_unexpected),
        "gitignore_leaks": leaks,
        "gitignore_leak_count": len(leaks),
    }


def verdicts(res: dict) -> list[tuple[str, str, str]]:
    """Return (metric, OK|WARN|RED, reason) tuples for each enforced invariant."""
    out = []
    leaks = int(res.get("gitignore_leak_count", 0))
    out.append((
        "gitignore_leaks",
        "RED" if leaks > 0 else "OK",
        f"{leaks} tracked-to-gitignored link(s) (target 0)",
    ))
    orphans = int(res.get("orphan_count", 0))
    o_status = "RED" if orphans >= ORPHAN_RED else ("WARN" if orphans >= ORPHAN_WARN else "OK")
    out.append(("orphans", o_status, f"{orphans} visible orphan(s) (target 0)"))
    share = float(res.get("main_share", 1.0))
    c_status = "RED" if share < MAIN_SHARE_RED else ("WARN" if share < MAIN_SHARE_WARN else "OK")
    out.append((
        "main_share",
        c_status,
        f"{share * 100:.1f}% in main component, {res.get('components', 1)} component(s) (target >= 99%)",
    ))
    cross = int(res.get("cross_unexpected_count", 0))
    out.append((
        "cross_domain",
        "WARN" if cross > 0 else "OK",
        f"{cross} unexpected Work <-> Personal edge(s) ({res.get('cross_allowed_count', 0)} allowlisted)",
    ))
    return out


def worst_status(res: dict) -> str:
    rank = {"OK": 0, "WARN": 1, "RED": 2}
    worst = "OK"
    for _, st, _ in verdicts(res):
        if rank[st] > rank[worst]:
            worst = st
    return worst


def stamp_payload(res: dict) -> dict:
    """Aggregate-only JSON payload embedded in the report (never carries filenames)."""
    return {
        "generated_at": res["generated_at"],
        "nodes": res["nodes"],
        "edges": res["edges"],
        "avg_degree": res["avg_degree"],
        "orphans": res["orphan_count"],
        "components": res["components"],
        "main_share": res["main_share"],
        "cross_unexpected": res["cross_unexpected_count"],
        "gitignore_leaks": res["gitignore_leak_count"],
    }


def render_markdown(res: dict) -> str:
    lines = [
        "# Vault Graph Report",
        "",
        f"_Generated {res['generated_at']}_",
        "",
        "## Summary",
        "",
        "| Metric | Value | Status |",
        "|---|---|---|",
    ]
    for metric, status, why in verdicts(res):
        lines.append(f"| {metric} | {why} | {status} |")
    lines += [
        f"| nodes | {res['nodes']} visible .md files | |",
        f"| edges | {res['edges']} unique undirected (avg degree {res['avg_degree']}) | |",
        "",
    ]

    if res["gitignore_leaks"]:
        lines.append(f"## Gitignore privacy leaks ({len(res['gitignore_leaks'])})")
        lines.append("")
        for src, tgt in res["gitignore_leaks"][:50]:
            lines.append(f"- `{src}` -> `{tgt}`")
        lines.append("")

    if res["orphans"]:
        lines.append(f"## Visible orphans ({len(res['orphans'])})")
        lines.append("")
        for o in res["orphans"][:100]:
            lines.append(f"- `{o}`")
        if len(res["orphans"]) > 100:
            lines.append(f"_... +{len(res['orphans']) - 100} more_")
        lines.append("")

    if res["islands"]:
        lines.append(f"## Disconnected islands ({len(res['islands'])})")
        lines.append("")
        for isl in res["islands"][:30]:
            sample = ", ".join(f"`{s}`" for s in isl["sample"])
            lines.append(f"- size {isl['size']}: {sample}")
        lines.append("")

    if res["cross_unexpected"]:
        lines.append(f"## Unexpected Work <-> Personal edges ({len(res['cross_unexpected'])})")
        lines.append("")
        for a, b in res["cross_unexpected"][:50]:
            lines.append(f"- `{a}` <-> `{b}`")
        lines.append("")

    lines.append(f"<!-- vault_graph: {json.dumps(stamp_payload(res), ensure_ascii=False)} -->")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Audit Obsidian-visible vault graph connectivity.")
    ap.add_argument("--json", action="store_true", help="Print full JSON result and do not write report.")
    ap.add_argument("--no-write", action="store_true", help="Print Markdown report to stdout without writing.")
    args = ap.parse_args(argv)

    res = compute()
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.no_write:
        print(render_markdown(res))
    else:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(REPORT, render_markdown(res))
        print(
            f"[ok] {REPORT.relative_to(VAULT)} "
            f"status={worst_status(res)} nodes={res['nodes']} orphans={res['orphan_count']} "
            f"components={res['components']} main_share={res['main_share']:.4f} "
            f"leaks={res['gitignore_leak_count']} cross_unexpected={res['cross_unexpected_count']}"
        )
    print(
        f"RUNLOG nodes={res['nodes']} orphans={res['orphan_count']} components={res['components']} "
        f"main_share={res['main_share']:.4f} leaks={res['gitignore_leak_count']} "
        f"cross_unexpected={res['cross_unexpected_count']}",
        file=sys.stderr if args.json else sys.stdout,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
