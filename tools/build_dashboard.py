#!/usr/bin/env python3
"""build_dashboard.py: regenerate personaldashboard.md from the vault.

Deterministic (no LLM): scans project notes, decisions, and beliefs and assembles
a single daily-driver surface. Run on cron Mon 05:00 / Fri 21:00 (Istanbul).

Sections: time-sensitive items, active projects, open loops, nudges (the Part-3
reminders: reflect / grade-decisions / feed-ideas), and the archived list.
"""
import os
import re
import sys
from datetime import datetime, date
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import calibrate  # noqa: E402

VAULT = Path(os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless"))
OUT = VAULT / "personaldashboard.md"
CADENCE = VAULT / "Thinking" / "Thinking Cadence.md"

# Deadlines come from project frontmatter (`deadline: YYYY-MM-DD`, optional
# `deadline_note:`), never from constants here. Recently passed ones stay
# visible for a short grace window so a missed date is not silently dropped.
DEADLINE_GRACE_DAYS = 14
DEADLINE_HORIZON_DAYS = 400

import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from owner_profile import COMPANY_AREA, GENERIC_PRIVATE_SEGMENTS, PRIVATE_SEGMENTS as PROFILE_PRIVATE_SEGMENTS  # noqa: E402
from i18n import t  # noqa: E402

FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)
PRIVATE = ("- Health", *GENERIC_PRIVATE_SEGMENTS, *PROFILE_PRIVATE_SEGMENTS)


def fm(text):
    m = FM_RE.match(text)
    out = {}
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def is_private(p: Path) -> bool:
    s = str(p)
    return any(x in s for x in PRIVATE)


PROJECT_BASES = (
    (VAULT / "Work", "Work"),
    (VAULT / COMPANY_AREA / "Partnerships", "Work"),
    (VAULT / "Personal", "Personal"),
)


def git_ignored_set(paths) -> set:
    """Subset of paths git ignores: they exist on one machine only, and this file is
    regenerated on two machines, so including them would make the output flap.
    One `git check-ignore --stdin` call for the whole list; one process per path
    cost ~40 spawns per walk and the walk runs three times per dashboard build."""
    import subprocess
    paths = [str(p) for p in paths]
    if not paths:
        return set()
    try:
        # -z: NUL-separated in and out, so non-ASCII names come back unquoted.
        r = subprocess.run(["git", "check-ignore", "--stdin", "-z"], cwd=VAULT,
                           input="\0".join(paths), capture_output=True, text=True, timeout=30)
        return {line for line in r.stdout.split("\0") if line}
    except (OSError, subprocess.SubprocessError):
        return set()


_PROJECT_NOTES = None


def project_notes():
    """(category, name, notes.md path) for every project; discovered once per run.
    kill_criteria and two sections of this module all walk the same list."""
    global _PROJECT_NOTES
    if _PROJECT_NOTES is None:
        found = []
        for base, cat in PROJECT_BASES:
            if not base.exists():
                continue
            for d in sorted(base.iterdir()):
                n = d / "notes.md"
                if d.is_dir() and n.exists() and not is_private(n):
                    found.append((cat, d.name, n))
        ignored = git_ignored_set(n for _, _, n in found)
        _PROJECT_NOTES = [row for row in found if str(row[2]) not in ignored]
    return iter(_PROJECT_NOTES)


def days(d: date) -> int:
    return (d - date.today()).days


def next_cadence_step():
    if not CADENCE.exists():
        return None
    for line in CADENCE.read_text(errors="ignore").splitlines():
        m = re.match(r"\s*- \[ \]\s+(.+)", line)
        if m:
            return m.group(1).strip()
    return None


# ---------------------------------------------------------------------------
# PROJECTS-ACTIVE.md: generated from notes.md files so the entry layer cannot
# rot. Hand edits are overwritten; change a project's notes.md instead.
# ---------------------------------------------------------------------------
PROJECTS_ACTIVE = VAULT / "_Agent-Context" / "PROJECTS-ACTIVE.md"
CONTEXT_FILE = VAULT / "_Agent-Context" / "CONTEXT.md"
STALE_DAYS = 60
LOG_DATE_RE = re.compile(r"^###\s+(\d{4}-\d{2}-\d{2})", re.M)
LINK_RE = re.compile(r"\[\[([^\]|#]+)")


def section(text, title):
    """Body of '## <title>' up to the next '## ' heading (empty if absent)."""
    m = re.search(r"^##\s+" + re.escape(title) + r"\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    if not m:
        return ""
    body = re.sub(r"<!--.*?-->", "", m.group(1), flags=re.S)
    return body.strip()


def clean(t):
    """House style: no em/en dashes; drop stray bold markers around a line."""
    s = t.replace(" \u2014 ", ", ").replace("\u2014", ", ").replace(" \u2013 ", ", ").replace("\u2013", "-")
    s = re.sub(r"\s*,\s*,", ",", s)
    return s.strip().strip("*").strip()


def first_line(body):
    for line in body.splitlines():
        t = line.strip()
        if t and not t.startswith("- [") and not t.startswith("###"):
            return clean(t.lstrip("-* "))
    return ""


def open_tasks(text):
    """Unchecked actions, excluding stop conditions and commented templates."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    in_kill = False
    for line in text.splitlines():
        heading = re.match(r"^#{1,2}\s+(.+?)\s*#*\s*$", line)
        if heading:
            in_kill = heading.group(1).casefold() == "kill criteria"
        if in_kill:
            continue
        m = re.match(r"\s*-\s*\[ \]\s+(\S.*)", line)
        if m and "~~" not in m.group(1):
            yield clean(m.group(1))


def next_action(text):
    for title in ("Next Action", "Next Actions"):
        body = section(text, title)
        for line in body.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "~~" in line:
                continue
            if re.fullmatch(r"[-*]\s+\[[ xX]\]", line):
                continue
            if re.match(r"^[-*]\s+\[[xX]\]", line):
                continue
            line = re.sub(r"^[-*]\s+(?:\[ \]\s*)?", "", line)
            if line.strip():
                return clean(line)
    return next(open_tasks(text), "")


def next_kill_criterion(text):
    """Earliest open '- [ ] YYYY-MM-DD | condition | consequence' line of ## Kill Criteria, or ''."""
    import kill_criteria
    rows = [r for r in kill_criteria.criteria_lines(text) if not r[0]]
    if not rows:
        return ""
    _, d, cond, cons = sorted(rows, key=lambda r: r[1])[0]
    return f"{d.isoformat()} | {cond}" + (f" | {cons}" if cons else "")


def last_log_date(text):
    """Latest dated log entry. Headings (`### YYYY-MM-DD`) and list lines
    (`- [YYYY-MM-DD]`) both count; UK Relocation uses the list form."""
    body = section(text, "Log") or section(text, "Logs")
    haystack = body or text
    dates = LOG_DATE_RE.findall(haystack)
    dates += re.findall(r"^-\s*\[(\d{4}-\d{2}-\d{2})\]", haystack, re.M)
    return max(dates) if dates else ""


def context_projects():
    """[[Names]] listed under '## Current Projects (Active)' in CONTEXT.md."""
    if not CONTEXT_FILE.exists():
        return []
    body = section(CONTEXT_FILE.read_text(errors="ignore"), "Current Projects (Active)")
    names = []
    for line in body.splitlines():
        if re.match(r"\s*\d+\.", line):
            names += [n.strip() for n in LINK_RE.findall(line)]
    return names


def has_home(name):
    """A folder under Work/ or Personal/ whose name matches, and whether it has notes.md."""
    key = name.casefold()
    for base in (VAULT / "Work", VAULT / "Personal", VAULT / COMPANY_AREA / "Partnerships"):
        if not base.exists():
            continue
        for d in base.iterdir():
            if d.is_dir() and (key in d.name.casefold() or d.name.casefold() in key):
                return d, (d / "notes.md").exists()
    return None, False


def build_projects_active(now):
    today = now.date()
    rows, parked, archived = [], [], []
    for cat, name, n in project_notes():
        text = n.read_text(errors="ignore")
        meta = fm(text)
        status = meta.get("status", "active").lower() or "active"
        if "archiv" in status:
            archived.append((cat, name))
            continue
        row = {
            "cat": cat, "name": name, "status": status,
            "outcome": first_line(section(text, "Outcome")),
            "current": first_line(section(text, "Current Status")),
            "next": next_action(text),
            "kill": next_kill_criterion(text),
            "last": last_log_date(text) or meta.get("date", ""),
        }
        try:
            age = (today - datetime.strptime(row["last"][:10], "%Y-%m-%d").date()).days
        except ValueError:
            age = None
        row["age"] = age
        (parked if ("hold" in status or "wind" in status) else rows).append(row)

    rows.sort(key=lambda r: r["last"], reverse=True)

    homeless = []
    for name in context_projects():
        folder, has_notes = has_home(name)
        if not has_notes:
            homeless.append((name, folder))

    L = []
    L.append("# Active Projects Summary")
    L.append("")
    L.append(t("build_dashboard.pa_auto_line"))
    L.append(t("build_dashboard.pa_hand_edit_line"))
    L.append(t("build_dashboard.pa_stale_line", days=STALE_DAYS, time=now.strftime('%Y-%m-%d %H:%M')))
    L.append("")
    L.append(t("build_dashboard.pa_active_heading", n=len(rows)))
    L.append("")
    for i, r in enumerate(rows, 1):
        flag = t("build_dashboard.pa_stale_flag") if (r["age"] is None or r["age"] > STALE_DAYS) else ""
        L.append(f"### {i}. {r['name']} [{r['cat']}]")
        L.append(t("build_dashboard.pa_status_row", status=r['status'],
                   last=r['last'] or t("build_dashboard.none"), flag=flag))
        if r["outcome"]:
            L.append(f"- **Outcome**: {r['outcome']}")
        if r["current"]:
            L.append(f"- **Current**: {r['current']}")
        L.append(f"- **Next action**: {r['next'] or t('build_dashboard.pa_no_open_item')}")
        L.append(t("build_dashboard.pa_kill_row", crit=r["kill"]) if r["kill"] else t("build_dashboard.pa_kill_none"))
        L.append(f"- **Link**: [[{r['name']}]] (`{r['cat']}/{r['name']}/notes.md`)")
        L.append("")

    if homeless:
        L.append(t("build_dashboard.pa_homeless_heading", n=len(homeless)))
        L.append("")
        L.append(t("build_dashboard.pa_homeless_note"))
        L.append("")
        for name, folder in homeless:
            where = (t("build_dashboard.pa_folder_exists", path=folder.relative_to(VAULT)) if folder
                     else t("build_dashboard.pa_no_folder"))
            L.append(f"- [[{name}]]: {where}")
        L.append("")

    if parked:
        L.append(t("build_dashboard.pa_parked_heading", n=len(parked)))
        L.append("")
        for r in parked:
            L.append(t("build_dashboard.pa_parked_row", cat=r['cat'], name=r['name'], status=r['status'],
                       last=r['last'] or t("build_dashboard.none")))
        L.append("")

    if archived:
        L.append(t("build_dashboard.pa_archive_heading", n=len(archived)))
        L.append("")
        L.append(", ".join(f"[{c}] {n}" for c, n in archived))
        L.append("")

    L.append("---")
    L.append(f"*Last updated: {now.strftime('%Y-%m-%d')} (auto)*")
    PROJECTS_ACTIVE.write_text("\n".join(L) + "\n")
    print(f"wrote {PROJECTS_ACTIVE.relative_to(VAULT)}: {len(rows)} active, {len(homeless)} homeless, {len(parked)} parked")



def main():
    now = datetime.now()
    active, archived = [], []
    open_loops = []

    deadlines = []
    for cat, name, n in project_notes():
        text = n.read_text(errors="ignore")
        meta = fm(text)
        status = meta.get("status", "active").lower()
        if "archiv" in status or "hold" in status or "wind" in status:
            archived.append((cat, name, status))
            continue
        active.append((cat, name))
        try:
            dl = datetime.strptime(meta.get("deadline", "")[:10], "%Y-%m-%d").date()
            delta = days(dl)
            if -DEADLINE_GRACE_DAYS <= delta <= DEADLINE_HORIZON_DAYS:
                deadlines.append((dl, delta, name, meta.get("deadline_note", "")))
        except ValueError:
            pass
        for task in open_tasks(text):
            open_loops.append((name, task))

    deadlines.sort()

    # Decisions awaiting revisit
    dec_dir = VAULT / "Thinking" / "Decisions"
    decisions = []
    if dec_dir.exists():
        for p in sorted(dec_dir.glob("*.md")):
            meta = fm(p.read_text(errors="ignore"))
            st = meta.get("status", "").lower()
            if st in ("pending", "deferred"):
                decisions.append((p.stem, st, meta.get("move_target", "")))

    # Stalest belief (oldest last_challenged)
    bel_dir = VAULT / "Thinking" / "Beliefs"
    beliefs = []
    if bel_dir.exists():
        for p in sorted(bel_dir.glob("*.md")):
            meta = fm(p.read_text(errors="ignore"))
            beliefs.append((p.stem, meta.get("last_challenged", "?")))
    beliefs.sort(key=lambda b: b[1])

    # Rotating reflective prompt
    prompts = [
        "What did I conclude or change my mind about this week?",
        "Which decision am I avoiding, and why?",
        "What is the top idea in my mind right now, and is it the one that should be?",
        "What did I learn that contradicts a current belief?",
        "Where am I reacting (emotion/ego/inertia) instead of thinking?",
    ]
    reflect = prompts[now.timetuple().tm_yday % len(prompts)]

    L = []
    L.append("---")
    L.append("type: dashboard")
    L.append(f"updated: {now.isoformat(timespec='minutes')}")
    L.append("note: auto-generated by tools/build_dashboard.py, do not hand-edit")
    L.append("---\n")
    L.append("# 🧭 Personal Dashboard\n")
    if (VAULT / "_Agent-Context/TODAY.md").exists():
        L.append(f"[[_Agent-Context/TODAY|{t('today_queue.title')}]]\n")
    L.append(f"_Updated {now.strftime('%a %Y-%m-%d %H:%M')} · regenerates Mon 05:00 / Fri 21:00_\n")

    L.append("## ⏰ Time-sensitive")
    for dl, delta, name, note in deadlines:
        when = t("build_dashboard.days_left", n=delta) if delta >= 0 else t("build_dashboard.days_overdue", n=-delta)
        L.append(f"- **{name}:** {when} ({dl.strftime('%d.%m.%Y')})" + (f": {note}" if note else ""))
    if not deadlines:
        L.append(t("build_dashboard.no_deadlines"))
    for name, st, tgt in decisions:
        L.append(f"- **Decision [{st}]:** {name}" + (f" → {tgt}" if tgt else ""))
    L.append("")

    # Kill criteria: the quit rule with a date (tools/kill_criteria.py). Breaches go red.
    import kill_criteria  # local import: kill_criteria imports this module for project discovery
    kc = kill_criteria.scan()
    L.append(t("build_dashboard.kill_heading"))
    for i in kc["breached"]:
        L.append(t("build_dashboard.kill_breached_row", project=i["project"], date=i["date"].isoformat(),
                   condition=i["condition"], consequence=(" | " + i["consequence"]) if i["consequence"] else "",
                   days=-i["days"]))
    for i in kc["due"]:
        L.append(t("build_dashboard.kill_due_row", project=i["project"], date=i["date"].isoformat(),
                   condition=i["condition"], consequence=(" | " + i["consequence"]) if i["consequence"] else "",
                   days=i["days"]))
    if not (kc["breached"] or kc["due"]):
        L.append(t("build_dashboard.kill_none", days=kill_criteria.DUE_SOON_DAYS))
    if kc["missing"]:
        names = ", ".join(n for _, n, _ in kc["missing"][:8]) + (" …" if len(kc["missing"]) > 8 else "")
        L.append(t("build_dashboard.kill_missing_line", n=len(kc["missing"]), names=names))
    L.append("")

    step = next_cadence_step()
    if step:
        L.append("## 🎯 This week's thinking step")
        L.append(f"- {clean(step)}")
        L.append("_(next unchecked in `Thinking/Thinking Cadence.md`, take one every week)_")
        L.append("")

    L.append(f"## 🟢 Active projects ({len(active)})")
    for cat, name in active:
        L.append(f"- [{cat}] {name}")
    L.append("")

    L.append(f"## 🔴 Open loops ({len(open_loops)})")
    for name, task in open_loops[:20]:
        L.append(f"- **{name}:** {task}")
    if len(open_loops) > 20:
        L.append(f"- … +{len(open_loops) - 20} more")
    L.append("")

    L.append("## 🧠 Nudges")
    L.append(f"- **Reflect:** {reflect}")
    if beliefs:
        L.append(f"- **Re-challenge belief** (stalest, last challenged {beliefs[0][1]}): {beliefs[0][0]}")
    due, needs_pred, _ = calibrate.scan()
    if due:
        L.append(f"- **Grade now (review passed):** {', '.join(n for n, _ in due)}")
    elif needs_pred:
        L.append(f"- **Add a prediction to:** {', '.join(needs_pred)}")
    elif decisions:
        L.append(f"- **Grade a decision:** score \"{decisions[0][0]}\" (predicted vs actual)")
    L.append("- **Feed the pipeline:** capture ≥1 seed idea this week (`_Templates/Idea.md` or /ideas)")
    L.append("")

    if archived:
        L.append(f"## 📦 Archived / on-hold ({len(archived)})")
        for cat, name, st in archived:
            L.append(f"- [{cat}] {name} ({st})")
        L.append("")

    OUT.write_text("\n".join(L))
    print(f"wrote {OUT.relative_to(VAULT)}: {len(active)} active, {len(open_loops)} open loops, {len(decisions)} decisions pending")

    build_projects_active(now)


if __name__ == "__main__":
    main()
