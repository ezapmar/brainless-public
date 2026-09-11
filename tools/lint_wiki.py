#!/usr/bin/env python3
"""
lint_wiki.py: integrity checks + auto-fix for the wiki layer.

Reports written to wiki/_lint-report.md.

Checks:
- broken [[links]] (real targets only; _commands/ examples and obvious placeholders are suppressed;
  targets that resolve anywhere in the vault, human homes included, count as informational refs)
- missing pages: unresolved targets aggregated by frequency (entity-page candidates, not errors)
- orphan files (no inbound or outbound links, excluding INDEX/_commands; INDEX links do not
  grant inbound, otherwise the auto-generated index would mask every orphan)
- stale summaries (source mtime newer than summary)
- missing frontmatter (lang, summary_en, compiled_at) on non-command files

Fix mode:
- --fix : backfill minimal required frontmatter on legacy wiki files (digests, summaries, articles, ideas, etc.)
- --fix-links : remap path/prefix mistakes to the correct target (non-destructive; unresolved links are left alone)
- --prune-links : with --fix-links, ALSO de-link unresolved targets (destructive; off by default)
- --dry-run with --fix : preview changes without writing

Usage:
  python3 tools/lint_wiki.py                  # report only (recommended in cron/nightly)
  python3 tools/lint_wiki.py --fix            # backfill frontmatter on legacy files
  python3 tools/lint_wiki.py --fix --dry-run  # preview what --fix would do
"""
import argparse
import os
import sys
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from owner_profile import LANG, COMPANY_AREA  # noqa: E402

# Company name as it appears in wikilinks/paths, derived from the profile so the linter
# stays deployment-neutral (e.g. company_area "Work/Acme Co" -> tokens acme co, acme-co, acme).
_C = COMPANY_AREA.strip("/").split("/")[-1].lower()
_COMPANY_TOKENS = tuple({_C, _C.replace(" ", "-"), _C.split()[0]}) if _C else ()

VAULT = Path(os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless"))
WIKI = VAULT / ".wiki"

LINK_RE = re.compile(r"\[\[([^\]\|#]+)(?:#[^\]\|]+)?(?:\|[^\]]+)?\]\]")
FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)


def parse_fm(text: str) -> dict:
    m = FM_RE.match(text)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm


# --- Link noise suppression & classification helpers ---

PLACEHOLDER_PATTERNS = [
    r"^Wikilink$", r"^Note Name$", r"^Note$", r"^Project( [A-Z])?$", r"^Belief( Note)?$",
    r"^Decision( Note| \u2014 .+)?$", r"^[AB]$", r"^Target( Note)?$", r"^Candidate( Note)?$",
    r"^Source( [AB])?$", r"^Intermediate( \d+)?$", r"^this note$", r"^New Idea Title$",
    r"^Belief Name$", r"^Daily \d{4}-\d{2}-\d{2}$",  # illustrative dates in command docs
    r"^Briefing \d{4}", r"^YYYY-MM-DD$",
]

PLACEHOLDER_RE = re.compile("|".join(PLACEHOLDER_PATTERNS), re.IGNORECASE)


def is_likely_placeholder(target: str) -> bool:
    t = target.strip()
    if PLACEHOLDER_RE.match(t):
        return True
    if len(t) < 3:
        return True
    if "..." in t:
        return True  # illustrative paths like [[wiki/...]] leaked from prompt text
    # very generic single words that only appear in prompt templates
    generic = {"links", "link", "topic-name", "note", "file", "path"}
    if t.lower() in generic:
        return True
    return False


def is_external_reference(target: str) -> bool:
    """Targets that intentionally point outside the generated wiki (the human homes: Work, Personal, etc.)."""
    t = target.strip().lower()
    if t.startswith(("work/", "personal/", "library/", "thinking/", "inbox/",
                     "archive/", "_agent-context/", "tools/")) or (_COMPANY_TOKENS and t.startswith(_COMPANY_TOKENS)):
        return True
    if any(x in t for x in (*_COMPANY_TOKENS, "belief", "decision")):
        return True
    # Also resolve against actual human-owned belief/decision/area files by stem
    return False


_VAULT_STEMS: set | None = None

def _vault_stem_index() -> set:
    """Lazy stem index over every .md in the vault outside dot-dirs and backups.

    Obsidian resolves [[Bare Title]] against the whole vault by basename, so a
    target with a matching stem anywhere in the human homes is NOT broken.
    """
    global _VAULT_STEMS
    if _VAULT_STEMS is None:
        _VAULT_STEMS = set()
        for p in VAULT.rglob("*.md"):
            rel = p.relative_to(VAULT).parts
            if any(part.startswith(".") for part in rel[:-1]) or rel[0] == "_Backup":
                continue
            _VAULT_STEMS.add(p.stem.lower())
            # 2026-09-04: every project hub is <Folder>/notes.md and carries
            # `aliases: ["<Folder>"]`, so [[<Folder>]] resolves in Obsidian.
            # Count the folder name as a stem too, otherwise every digest link
            # to a project reads as broken (188 false positives).
            if p.name == "notes.md":
                _VAULT_STEMS.add(p.parent.name.lower())
    return _VAULT_STEMS


def resolve_against_raw(target: str) -> bool:
    """Check if a bare title matches any note in the human-owned tree (whole vault, Obsidian-style)."""
    stem = Path(target).stem.lower().strip()
    return stem in _vault_stem_index()


def extract_summary_en(body: str, path: Path) -> str:
    """Crude but safe extraction of a one-paragraph English summary from legacy content."""
    # For digests, prefer the Executive Summary section
    if "digests/" in str(path):
        m = re.search(r"## Executive Summary.*?\n(.*?)(?:\n##|\Z)", body, re.S | re.I)
        if m:
            para = m.group(1).strip().split("\n\n")[0].strip()
            if para:
                return " ".join(para.split())[:280]

    # General: first non-empty paragraph after the first heading
    lines = [l.strip() for l in body.splitlines() if l.strip()]
    paras = []
    current = []
    for line in lines:
        if line.startswith("#") and current:
            break
        if line.startswith("#"):
            continue
        if not line:
            if current:
                paras.append(" ".join(current))
                current = []
            continue
        current.append(line)
    if current:
        paras.append(" ".join(current))

    for p in paras:
        if len(p) > 40:
            return p[:280]

    return "Legacy content backfilled by lint --fix. Review and improve this summary."


def ensure_frontmatter(path: Path, dry_run: bool = False) -> list[str]:
    """Ensure required frontmatter keys exist. Returns list of keys that were (or would be) added."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    fm = parse_fm(text)
    required = ["lang", "summary_en", "compiled_at"]
    missing = [k for k in required if k not in fm]
    if not missing:
        return []

    rel = str(path.relative_to(VAULT))
    now = datetime.now().isoformat(timespec="seconds")

    additions: dict[str, str] = {}

    if "lang" in missing:
        if "digests/" in rel or "/ideas/" in rel or "/articles/" in rel or "/moc/" in rel:
            additions["lang"] = "en"
        elif "summaries/" in rel:
            # Summaries are written in the owner's output language (PROFILE.md).
            additions["lang"] = LANG
        else:
            additions["lang"] = "en"

    if "summary_en" in missing:
        body = text
        # strip existing frontmatter if present for extraction
        if FM_RE.match(text):
            parts = FM_RE.split(text, maxsplit=1)
            body = parts[-1] if parts else text
        additions["summary_en"] = extract_summary_en(body, path)

    if "compiled_at" in missing:
        additions["compiled_at"] = now

    if not additions:
        return []

    if dry_run:
        return list(additions.keys())

    # Build new frontmatter block
    # Preserve any existing keys, then append the new ones (simple but effective)
    existing_lines = []
    if FM_RE.match(text):
        m = FM_RE.match(text)
        for line in m.group(1).splitlines():
            if ":" in line:
                k = line.split(":", 1)[0].strip()
                if k not in additions:  # keep original value
                    existing_lines.append(line.strip())
    for k, v in additions.items():
        existing_lines.append(f"{k}: {v}")

    new_fm = "---\n" + "\n".join(existing_lines) + "\n---\n"

    # Remove old frontmatter if present and prepend the new one
    body_without_fm = FM_RE.sub("", text, count=1).lstrip("\n")
    new_text = new_fm + "\n" + body_without_fm if body_without_fm else new_fm

    try:
        path.write_text(new_text, encoding="utf-8")
        return list(additions.keys())
    except Exception as e:
        print(f"[fix error] {path.relative_to(VAULT)}: {e}")
        return []


def all_wiki_files():
    return sorted(p for p in WIKI.rglob("*.md") if "_lint-report" not in p.name)


# Captures target + optional #heading + optional |alias, so we can rewrite a link
# while preserving its display alias.
FIX_LINK_RE = re.compile(r"\[\[([^\]\|#]+)(#[^\]\|]+)?(\|[^\]]+)?\]\]")


def fix_links_in_file(path: Path, broken_targets: set, remap, dry_run: bool = False,
                      prune: bool = False) -> dict:
    """Repair the broken links in one file.

    Only targets in `broken_targets` (the linter's own real-broken set for this
    file) are touched. A target that `remap` can resolve becomes a working
    `[[stem]]` link. Unresolved targets are LEFT ALONE by default: an unresolved
    wikilink is a missing-page signal (and harmless in Obsidian), not an error.
    Only with `prune` are they de-linked to plain display text.
    """
    text = path.read_text(errors="ignore")
    counts = {"remapped": 0, "delinked": 0}

    def repl(m):
        target = m.group(1).strip()
        if target not in broken_targets:
            return m.group(0)
        alias = m.group(3)[1:].strip() if m.group(3) else None
        hit = remap(target)
        if hit is not None:
            counts["remapped"] += 1
            return f"[[{hit.stem}|{alias}]]" if alias else f"[[{hit.stem}]]"
        if not prune:
            return m.group(0)
        counts["delinked"] += 1
        return alias or Path(target.split("#")[0]).name

    new_text = FIX_LINK_RE.sub(repl, text)
    if new_text != text and not dry_run:
        path.write_text(new_text, encoding="utf-8")
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true",
                    help="Backfill missing required frontmatter (lang, summary_en, compiled_at) on wiki files")
    ap.add_argument("--dry-run", action="store_true",
                    help="With --fix: show what would be changed but do not write files")
    ap.add_argument("--fix-links", action="store_true",
                    help="Repair real broken [[links]] in .wiki/: remap path/prefix mistakes to the "
                         "correct target; unresolved targets are left as-is (missing-page signals)")
    ap.add_argument("--prune-links", action="store_true",
                    help="With --fix-links: de-link (keep text, drop [[ ]]) targets that resolve "
                         "nowhere. Destructive; erases the missing-page signal. Off by default.")
    ap.add_argument("--no-llm", action="store_true", help="(deprecated) kept for compatibility")
    args = ap.parse_args()

    files = all_wiki_files()
    by_stem = {}
    by_relpath = {}
    inbound = {p: 0 for p in files}
    outbound = {p: 0 for p in files}

    for p in files:
        rel = str(p.relative_to(VAULT))
        by_stem.setdefault(p.stem.lower(), []).append(p)
        by_relpath[rel] = p
        by_relpath[rel.replace(".md", "")] = p
        by_relpath[rel.replace(".md", "").lower()] = p

    # --- Link collection with noise suppression ---
    real_broken: list[tuple[Path, str]] = []
    external_refs: list[tuple[Path, str]] = []
    placeholder_count = 0

    for p in files:
        if "_commands" in str(p):
            continue  # prompt templates deliberately contain [[Example]] and [[A]] style links
        if p.name == "INDEX.md":
            continue  # auto-generated; links everything, would mask every orphan via fake inbound

        text = p.read_text(errors="ignore")
        for m in LINK_RE.finditer(text):
            target = m.group(1).strip()
            outbound[p] += 1

            if is_likely_placeholder(target):
                placeholder_count += 1
                continue

            # resolution attempts
            cands = [
                target,
                target + ".md",
                target.lower(),
                f".wiki/{target}.md",
                f".wiki/{target}",
                f"wiki/{target}.md",
                f"wiki/{target}",
                str(Path(target).with_suffix(".md")),
            ]
            hit = None
            for c in cands:
                if c in by_relpath:
                    hit = by_relpath[c]
                    break
            if not hit:
                stem_hits = by_stem.get(Path(target).stem.lower(), [])
                if stem_hits:
                    hit = stem_hits[0]

            if hit:
                inbound[hit] += 1
            else:
                if is_external_reference(target) or resolve_against_raw(target):
                    external_refs.append((p, target))
                else:
                    real_broken.append((p, target))

    # --- Optional: repair broken links (remap + de-link) ---
    links_remapped = 0
    links_delinked = 0
    if args.fix_links:
        def resolve(target):
            cands = [
                target, target + ".md", target.lower(),
                f".wiki/{target}.md", f".wiki/{target}",
                f"wiki/{target}.md", f"wiki/{target}",
                str(Path(target).with_suffix(".md")),
            ]
            for c in cands:
                if c in by_relpath:
                    return by_relpath[c]
            stem_hits = by_stem.get(Path(target).stem.lower(), [])
            return stem_hits[0] if stem_hits else None

        def remap(target):
            variants = []
            low = target.lower()
            for pre in ("wiki/", "raw/", ".wiki/"):
                if low.startswith(pre):
                    variants.append(target[len(pre):])
            if "Resources/Books/" in target:
                variants.append(target.replace("Resources/Books/", "Library/Books/"))
            variants.append(Path(target).name)   # basename-only
            for v in variants:
                hit = resolve(v)
                if hit is not None:
                    return hit
            return None

        broken_by_file: dict[Path, set] = {}
        for p, target in real_broken:
            broken_by_file.setdefault(p, set()).add(target)
        for p, targets in broken_by_file.items():
            c = fix_links_in_file(p, targets, remap, dry_run=args.dry_run,
                                  prune=args.prune_links)
            links_remapped += c["remapped"]
            links_delinked += c["delinked"]

    orphans = [
        p for p in files
        if inbound[p] == 0 and outbound[p] == 0
        and "INDEX" not in p.name and "_commands" not in str(p)
    ]

    # stale summaries (based on frontmatter source pointer)
    stale = []
    summaries_dir = WIKI / "summaries"
    if summaries_dir.exists():
        for s in summaries_dir.glob("*.md"):
            fm = parse_fm(s.read_text(errors="ignore"))
            src = fm.get("source", "")
            if not src:
                continue
            src_path = VAULT / src
            if src_path.exists() and src_path.stat().st_mtime > s.stat().st_mtime + 1:
                stale.append((s, src_path))

    # --- Frontmatter issues + optional fix ---
    fm_issues: list[tuple[Path, list[str]]] = []
    fixed: list[tuple[Path, list[str]]] = []

    for p in files:
        if "_commands" in str(p) or p.name == "INDEX.md":
            continue
        fm = parse_fm(p.read_text(errors="ignore"))
        missing = [k for k in ("lang", "summary_en", "compiled_at") if k not in fm]
        if missing:
            fm_issues.append((p, missing))
            if args.fix:
                added = ensure_frontmatter(p, dry_run=args.dry_run)
                if added:
                    fixed.append((p, added))

    # --- Write improved report ---
    ts = datetime.now().isoformat(timespec="seconds")
    lines = [
        "# Wiki Lint Report\n",
        f"_Generated {ts}_\n",
        "## Summary\n",
        f"- files scanned: {len(files)}",
        f"- broken links (real): {len(real_broken)}   (wiki/_commands/ link scanning skipped, those files only contain prompt examples)",
        *([f"- broken links fixed: {links_remapped} remapped, {links_delinked} de-linked"
           + (" (dry-run, no files written)" if args.dry_run else "")] if args.fix_links else []),
        f"- external / raw references (informational): {len(external_refs)}",
        f"- orphan files: {len(orphans)}",
        f"- stale summaries: {len(stale)}",
        f"- frontmatter issues: {len(fm_issues)}",
    ]
    if args.fix:
        lines.append(f"- frontmatter fixes applied (or would apply): {len(fixed)}")
    lines.append("")

    def section(title, items, fmt, limit=150):
        lines.append(f"## {title} ({len(items)})\n")
        for it in items[:limit]:
            lines.append(fmt(it))
        if len(items) > limit:
            lines.append(f"_... +{len(items) - limit} more_")
        lines.append("")

    if real_broken:
        section("Real broken links (actionable)", real_broken,
                lambda x: f"- `{x[0].relative_to(VAULT)}` → `[[{x[1]}]]`")
    else:
        lines.append("## Real broken links (actionable)\nNone found after suppressing template noise.\n")

    # Missing pages: unresolved targets grouped by demand. These are entity-page
    # candidates (concepts the wiki keeps naming but nobody has written), not errors.
    missing_counter = Counter(t for _, t in real_broken)
    if missing_counter:
        lines.append(f"## Missing pages (entity candidates, by demand) ({len(missing_counter)})\n")
        for target, n in missing_counter.most_common():
            lines.append(f"- `[[{target}]]` wanted {n}x")
        lines.append("")

    if external_refs:
        section("External / raw / Kolay references (usually intentional)", external_refs,
                lambda x: f"- `{x[0].relative_to(VAULT)}` → `[[{x[1]}]]` (outside wiki)")

    section("Orphan files (no links in or out)", orphans,
            lambda p: f"- `{p.relative_to(VAULT)}`")

    section("Stale summaries (source newer than wiki copy)", stale,
            lambda x: f"- `{x[0].relative_to(VAULT)}` (source `{x[1].relative_to(VAULT)}`)")

    if fm_issues:
        section("Missing frontmatter (lang, summary_en, compiled_at)", fm_issues,
                lambda x: f"- `{x[0].relative_to(VAULT)}` missing: {', '.join(x[1])}")

    if args.fix and fixed:
        lines.append("## Frontmatter fixes " + ("(dry-run, no files written)" if args.dry_run else "(applied)") + f" ({len(fixed)})\n")
        for p, keys in fixed[:100]:
            lines.append(f"- `{p.relative_to(VAULT)}` ← added {', '.join(keys)}")
        if len(fixed) > 100:
            lines.append(f"_... +{len(fixed)-100} more_")
        lines.append("")

    # Actionable recommendations (concise)
    lines.append("## Recommended actions\n")
    recs = []
    if real_broken:
        recs.append("1. Create entity pages for the most-wanted missing pages above (highest demand first); "
                    "remap or alias genuine path mistakes. De-linking is opt-in via --prune-links.")
    if fm_issues and not args.fix:
        recs.append("2. Run `python3 tools/lint_wiki.py --fix` to backfill frontmatter on legacy files (safe for summaries/digests/articles).")
    if fixed and args.dry_run:
        recs.append("2. Re-run without --dry-run to apply the frontmatter changes shown above.")
    if stale:
        recs.append("3. Re-run `python3 tools/compile_resources.py` (or the affected phase) for stale summaries.")
    if not recs:
        recs.append("No high-priority hygiene issues detected.")
    for r in recs:
        lines.append(r)
    lines.append("")

    out = WIKI / "_lint-report.md"
    out.write_text("\n".join(lines))
    print(f"[ok] {out.relative_to(VAULT)}")
    print(f"real_broken={len(real_broken)} placeholders_suppressed={placeholder_count} "
          f"external={len(external_refs)} orphans={len(orphans)} stale={len(stale)} "
          f"fm_issues={len(fm_issues)} fixed={len(fixed)}")


if __name__ == "__main__":
    main()
