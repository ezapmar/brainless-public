#!/usr/bin/env python3
"""attention_audit.py: separate stated priorities from inferred activity.

Writes _Agent-Context/ATTENTION.md deterministically (no LLM, $0):
- Stated priorities: read from _Agent-Context/PROJECTS-ACTIVE.md.
- Inferred activity (last N days, default 7): measured from recent files under
  Thinking/Daily/, Inbox/, Work/, Personal/, and .wiki/summaries/.
- Attention gaps: stated active projects with zero recent touches, and active
  folders receiving touches that are not listed as stated active projects.

Usage:
  python3 tools/attention_audit.py              # write _Agent-Context/ATTENTION.md
  python3 tools/attention_audit.py --dry-run    # print without writing
  python3 tools/attention_audit.py --days 14    # look back 14 days
"""
import argparse
import os
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

VAULT = Path(os.environ.get("BRAINLESS_VAULT") or Path(__file__).resolve().parents[1])
TOOLS = VAULT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from fsutil import atomic_write
import owner_profile

PROJECT_HEADING_RE = re.compile(r"^###\s+\d+\.\s+(.+?)\s+\[(Work|Personal|[^\]]+)\]\s*$")
LAST_LOG_RE = re.compile(r"Son log:\s*(\d{4}-\d{2}-\d{2})")
NEXT_ACTION_RE = re.compile(r"^\-\s+\*\*Next action\*\*:\s*(.+)$")
LINK_PATH_RE = re.compile(r"`([^`]+\.md)`")


def fold(text: str) -> str:
    """Fold Turkish and Latin diacritics for case-insensitive matching."""
    s = unicodedata.normalize("NFC", text)
    s = s.replace("ı", "i").replace("İ", "i").replace("I", "i")
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.lower()).strip()


def is_protected_path(rel_posix: str) -> bool:
    """True if a vault-relative path belongs to a protected home or private segment."""
    folded_rel = fold(rel_posix)
    parts = [fold(part) for part in Path(rel_posix).parts]
    for home in owner_profile.PROTECTED_HOMES:
        fh = fold(home)
        if folded_rel == fh or folded_rel.startswith(fh + "/"):
            return True
    for seg in (*owner_profile.PRIVATE_SEGMENTS, *owner_profile.GENERIC_PRIVATE_SEGMENTS):
        fs = fold(seg)
        if fs and any(p == fs for p in parts):
            return True
    for suf in owner_profile.PRIVATE_SUFFIXES:
        fs = fold(suf)
        if fs and any(p.endswith(fs) for p in parts):
            return True
    for frag in owner_profile.PRIVATE_NAME_PARTS:
        ff = fold(frag)
        if ff and ff in folded_rel:
            return True
    return False


def parse_stated_priorities(text: str) -> list[dict]:
    """Extract active projects from _Agent-Context/PROJECTS-ACTIVE.md."""
    projects: list[dict] = []
    current: dict | None = None
    in_active = False

    for line in text.splitlines():
        if line.startswith("## "):
            in_active = "aktif" in fold(line) or "active" in fold(line)
            if not in_active:
                current = None
            continue
        if not in_active:
            continue

        m = PROJECT_HEADING_RE.match(line.strip())
        if m:
            current = {
                "name": m.group(1).strip(),
                "domain": m.group(2).strip(),
                "last_log": "",
                "next_action": "",
                "path": "",
            }
            projects.append(current)
            continue

        if current is None:
            continue

        lm = LAST_LOG_RE.search(line)
        if lm and not current["last_log"]:
            current["last_log"] = lm.group(1)
        nm = NEXT_ACTION_RE.match(line.strip())
        if nm:
            current["next_action"] = nm.group(1).strip()
        if line.strip().startswith("- **Link**:"):
            pm = LINK_PATH_RE.search(line)
            if pm:
                current["path"] = pm.group(1).strip()

    return projects


def _iter_recent_markdown(root: Path, cutoff_ts: float, vault: Path) -> list[Path]:
    if not root.exists():
        return []
    hits: list[Path] = []
    for p in root.rglob("*.md"):
        if any(part.startswith(".") for part in p.relative_to(root).parts):
            continue
        rel = p.relative_to(vault).as_posix()
        if is_protected_path(rel):
            continue
        try:
            if p.stat().st_mtime >= cutoff_ts:
                hits.append(p)
        except OSError:
            continue
    return hits


def _project_tokens(project: dict) -> list[str]:
    tokens = [fold(project["name"])]
    if project["path"]:
        parts = Path(project["path"]).parts
        if len(parts) >= 2:
            folder_tok = fold(parts[1])
            if folder_tok and folder_tok not in tokens:
                tokens.append(folder_tok)
    return [t for t in tokens if len(t) >= 3]


def scan_activity(vault: Path, stated: list[dict], days: int = 7,
                  now: datetime | None = None) -> dict:
    """Measure inferred activity over the last `days` days across vault surfaces."""
    ref = now or datetime.now()
    cutoff_ts = (ref - timedelta(days=days)).timestamp()

    daily_files = _iter_recent_markdown(vault / "Thinking" / "Daily", cutoff_ts, vault)
    inbox_files = _iter_recent_markdown(vault / "Inbox", cutoff_ts, vault)
    work_files = _iter_recent_markdown(vault / "Work", cutoff_ts, vault)
    personal_files = _iter_recent_markdown(vault / "Personal", cutoff_ts, vault)

    project_hits: Counter[str] = Counter()
    project_sources: dict[str, set[str]] = {p["name"]: set() for p in stated}
    token_map = [(p["name"], _project_tokens(p)) for p in stated]

    # 1. Mentions inside daily notes and inbox captures.
    for src_group in (daily_files, inbox_files):
        for p in src_group:
            try:
                body = fold(p.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
            rel = p.relative_to(vault).as_posix()
            for name, tokens in token_map:
                if any(tok in body for tok in tokens):
                    project_hits[name] += 1
                    project_sources[name].add(rel)

    # 2. Direct file edits inside Work/<folder>/ and Personal/<folder>/.
    stated_folders = {
        fold(Path(p["path"]).parts[1]): p["name"]
        for p in stated
        if p["path"] and len(Path(p["path"]).parts) >= 2
    }
    for p in stated:
        stated_folders.setdefault(fold(p["name"]), p["name"])

    unstated_folders: Counter[str] = Counter()
    for p in (*work_files, *personal_files):
        rel_parts = p.relative_to(vault).parts
        if len(rel_parts) < 2:
            continue
        area, folder = rel_parts[0], rel_parts[1]
        if folder.endswith(".md"):
            continue
        fkey = fold(folder)
        if fkey in stated_folders:
            name = stated_folders[fkey]
            project_hits[name] += 1
            project_sources[name].add(p.relative_to(vault).as_posix())
        else:
            unstated_folders[f"{area}/{folder}"] += 1

    return {
        "counts": {
            "daily_notes": len(daily_files),
            "inbox_notes": len(inbox_files),
            "work_edits": len(work_files),
            "personal_edits": len(personal_files),
        },
        "project_hits": project_hits,
        "project_sources": {k: sorted(v)[:5] for k, v in project_sources.items()},
        "unstated_folders": unstated_folders,
    }


def render_report(stated: list[dict], activity: dict, days: int,
                  now: datetime | None = None) -> str:
    ref = now or datetime.now()
    checked = ref.strftime("%Y-%m-%d %H:%M")
    hits = activity["project_hits"]
    counts = activity["counts"]

    active_stated = sorted(
        [p for p in stated if hits.get(p["name"], 0) > 0],
        key=lambda p: (-hits[p["name"]], p["name"]),
    )
    quiet_stated = [p for p in stated if hits.get(p["name"], 0) == 0]
    unstated = activity["unstated_folders"].most_common(10)

    lines = [
        "# Attention Audit (Stated vs Inferred)",
        "",
        f"> Generated automatically by `tools/attention_audit.py`. Window: last {days} days.",
        f"> status_checked: {checked}",
        "",
        "## Signals Scanned (Facts)",
        f"- Recent daily notes (`Thinking/Daily/`): {counts['daily_notes']}",
        f"- Recent inbox captures (`Inbox/`): {counts['inbox_notes']}",
        f"- Recent edits in `Work/`: {counts['work_edits']}",
        f"- Recent edits in `Personal/`: {counts['personal_edits']}",
        f"- Stated active projects (`PROJECTS-ACTIVE.md`): {len(stated)}",
        "",
        f"## Stated Priorities With Recent Activity ({len(active_stated)})",
    ]
    if active_stated:
        for p in active_stated:
            n = hits[p["name"]]
            sample = ", ".join(f"`{s}`" for s in activity["project_sources"].get(p["name"], [])[:3])
            lines.append(f"- [[{p['name']}]] [{p['domain']}]: {n} touches ({sample})")
    else:
        lines.append("- None in this window.")

    lines += ["", f"## Inferred Activity Outside Stated Projects ({len(unstated)})"]
    if unstated:
        for folder, n in unstated:
            lines.append(f"- `{folder}`: {n} edited files (not listed in active projects)")
    else:
        lines.append("- None.")

    lines += ["", f"## Attention Gaps: Stated Priorities With Zero Touches ({len(quiet_stated)})"]
    if quiet_stated:
        for p in quiet_stated:
            last = f"last log {p['last_log']}" if p["last_log"] else "no log date"
            lines.append(f"- [[{p['name']}]] [{p['domain']}]: 0 touches in {days} days ({last})")
    else:
        lines.append("- Every stated active project saw activity in this window.")

    lines += [
        "",
        "## Your Decision",
        "- Promote a high-activity unstated folder into `PROJECTS-ACTIVE.md` (add `notes.md`) or park quiet stated projects as `on-hold`.",
        "",
    ]
    return "\n".join(lines)


def run_audit(vault: Path = VAULT, days: int = 7, dry_run: bool = False,
              now: datetime | None = None) -> tuple[str, dict]:
    projects_file = vault / "_Agent-Context" / "PROJECTS-ACTIVE.md"
    stated = parse_stated_priorities(projects_file.read_text(encoding="utf-8")) if projects_file.exists() else []
    activity = scan_activity(vault, stated, days=days, now=now)
    report = render_report(stated, activity, days=days, now=now)
    if not dry_run:
        out = vault / "_Agent-Context" / "ATTENTION.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(out, report)
    quiet = sum(1 for p in stated if activity["project_hits"].get(p["name"], 0) == 0)
    summary = {
        "stated": len(stated),
        "active": len(stated) - quiet,
        "quiet": quiet,
        "unstated": len(activity["unstated_folders"]),
    }
    return report, summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Compare stated priorities with inferred recent activity.")
    ap.add_argument("--days", type=int, default=7, help="Lookback window in days (default: 7)")
    ap.add_argument("--dry-run", action="store_true", help="Print report without writing _Agent-Context/ATTENTION.md")
    args = ap.parse_args(argv)

    report, summary = run_audit(VAULT, days=args.days, dry_run=args.dry_run)
    if args.dry_run:
        print(report)
    else:
        print("[ok] _Agent-Context/ATTENTION.md")
    print(
        f"RUNLOG attention_stated={summary['stated']} active={summary['active']} "
        f"quiet={summary['quiet']} unstated={summary['unstated']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
