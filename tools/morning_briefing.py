#!/usr/bin/env python3
"""morning_briefing.py: the morning half of the day's briefing, without the desktop app.

The morning briefing was a Claude scheduled task that fires only while the
desktop app is open. On a morning the app was closed there was no briefing and
nothing said so until the health check noticed after 08:30 (19/11/2026 was one
such morning). Almost every section of it is a verbatim copy of a file another
job already wrote, so this builds those sections with no model at all and runs
from the morning set (cron_wrapper.sh), where a sleeping Mac catches up on wake.

  health     HEALTH.md status lines and rows, then the breached kill criteria
  calendar   today's events from Google Calendar (read-only token, optional)
  promises   open items under the promises heading of TASKS.md, older than 2 days;
             the most recent MAX_PROMISES, because the list ran to 190 on
             19/11/2026 and the oldest fifteen were all from August
  crm        CRM.md flags (first MAX_FLAGS) and the last 24 hours of changes
  wiki       WIKI-CHANGES.md when under 36 hours old
  revisit    RESURFACE.md when under 7 days old
  drift      CONTEXT-DRIFT.md when it reports any
  actions    at most five, by rule: engine red, kill criteria due soon, flagged
             conflicts from last night, then the freshest overdue promises

Not here: the e-mail summary. That needs a model reading the inbox, which the
desktop task does through its connector; this job says so in one line.

One file per day, Daily Briefings/daily-briefing-YYYY-MM-DD.md. The block sits
between two markers at the top of the file, so a second run replaces it and the
evening close-out below it is never touched. Weekdays only.

Usage:
  python3 tools/morning_briefing.py             write today's block
  python3 tools/morning_briefing.py --dry-run   print it
  python3 tools/morning_briefing.py --force     also on a weekend
"""
import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

from fsutil import atomic_write
from i18n import t
from paths import vault_root

VAULT = Path(vault_root())
CTX = VAULT / "_Agent-Context"
OUT_DIR = VAULT / "Daily Briefings"
START, END = "<!-- morning:start -->", "<!-- morning:end -->"
PROMISE_DAYS = 2
MAX_PROMISES = 10
MAX_FLAGGED = 6
MAX_FLAGS = 8
WIKI_HOURS = 36
RESURFACE_DAYS = 7
_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2}))?")


def read(name: str) -> str:
    try:
        return (CTX / name).read_text(encoding="utf-8")
    except OSError:
        return ""


def section(text: str, heading: str) -> list[str]:
    """Non-empty lines under a '## ' heading that starts with `heading`."""
    head = heading.lstrip("# ").split("(")[0].strip()
    m = re.search(r"^##\s+" + re.escape(head) + r"[^\n]*\n(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    return [ln for ln in (m.group(1).splitlines() if m else []) if ln.strip()]


def stamp(text: str) -> datetime | None:
    """The first date (and time, when there is one) in a file's opening lines."""
    m = _DATE.search("\n".join(text.splitlines()[:6]))
    if not m:
        return None
    return datetime.strptime(f"{m[1]} {m[2] or '00:00'}", "%Y-%m-%d %H:%M")


# ─── Sections ────────────────────────────────────────────────────

def health() -> tuple[list[str], bool]:
    """(lines, engine_red). Status lines and rows as HEALTH.md has them."""
    lines = [ln for ln in read("HEALTH.md").splitlines()[1:] if ln.strip() and not ln.startswith(">")]
    status = next((ln for ln in lines if ln.startswith("**")), "")
    red = "🔴" in status
    breached = [ln for ln in section(read("KILL-CRITERIA.md"), t("kill_criteria.breached_heading", n=0))
                if ln.startswith("- ")]
    out = ([t("morning_briefing.red_flag")] if red else []) + lines
    if not lines:
        out = [t("morning_briefing.no_health")]
    return out + ([""] + breached if breached else []), red


def calendar(today: datetime) -> list[str]:
    """Today's events as a table, or one line saying why there is none."""
    try:
        import meeting_brief
        service = meeting_brief.get_service()
        day = today.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        items = service.events().list(
            calendarId="primary", singleEvents=True, orderBy="startTime",
            timeMin=day.isoformat(), timeMax=(day + timedelta(days=1)).isoformat()).execute().get("items", [])
    except Exception as e:  # no token, no library, no network: the briefing still goes out
        return [t("morning_briefing.calendar_unavailable", error=type(e).__name__)]
    rows, prev_end = [], None
    for e in items:
        if e.get("status") == "cancelled":
            continue
        mine = next((a.get("responseStatus", "") for a in e.get("attendees", []) if a.get("self")), "")
        if mine == "declined":
            continue
        start, end = e.get("start", {}), e.get("end", {})
        if "dateTime" in start:
            s, f = datetime.fromisoformat(start["dateTime"]), datetime.fromisoformat(end["dateTime"])
            when = t("morning_briefing.time_range", start=f"{s.astimezone():%H:%M}", end=f"{f.astimezone():%H:%M}")
            clash = bool(prev_end and s < prev_end)
            prev_end = max(prev_end, f) if prev_end else f
        else:
            when, clash = t("morning_briefing.all_day"), False
        people = len([a for a in e.get("attendees", []) if not a.get("self")])
        title = (e.get("summary") or "?").replace("|", "/")
        rows.append(f"| {when} | {title} | {people or ''} | {mine or ''} | {'⚠️' if clash else ''} |")
    if not rows:
        return [t("morning_briefing.calendar_empty")]
    return [t("morning_briefing.calendar_header"), "|---|---|---|---|---|", *rows]


def promises(today: datetime) -> list[str]:
    rows = []
    for ln in section(read("TASKS.md"), t("nightly_processor.tasks_section")):
        if not ln.lstrip().startswith("- [ ]"):
            continue
        dates = _DATE.findall(ln)
        if dates and datetime.strptime(dates[-1][0], "%Y-%m-%d") <= today - timedelta(days=PROMISE_DAYS):
            rows.append((dates[-1][0], ln.strip()))
    rows.sort(reverse=True)                 # freshest first: those can still be kept
    out = [ln for _, ln in rows[:MAX_PROMISES]]
    if len(rows) > MAX_PROMISES:
        out.append(t("morning_briefing.more", n=len(rows) - MAX_PROMISES, file="_Agent-Context/TASKS.md"))
    return out


def crm(today: datetime) -> tuple[list[str], str]:
    """(section lines, a line for the health block when the snapshot is red or stale)."""
    text = read("CRM.md")
    if not text:
        return [], ""
    flags = [ln for ln in section(text, t("crm_capture.flags")) if ln.startswith("- ")]
    changes = [ln for ln in section(text, t("crm_capture.changes")) if ln.startswith("- ")]
    status = next((ln for ln in text.splitlines() if ln.startswith("**")), "")
    when = stamp(text)
    stale = bool(when and today - when > timedelta(hours=26))
    note = t("morning_briefing.crm_note", status=status) if ("🔴" in status or stale) else ""
    out = []
    if flags:
        out += [t("morning_briefing.crm_flags"), *flags[:MAX_FLAGS]]
        if len(flags) > MAX_FLAGS:
            out.append(t("morning_briefing.more", n=len(flags) - MAX_FLAGS, file="_Agent-Context/CRM.md"))
    if changes:
        out += ["", t("morning_briefing.crm_changes"), *changes]
    return out, note


def wiki(today: datetime) -> tuple[list[str], str]:
    text = read("WIKI-CHANGES.md")
    when = stamp(text)
    if not text or not when:
        return [], ""
    if today - when > timedelta(hours=WIKI_HOURS):
        return [], t("morning_briefing.wiki_stale", time=when.strftime("%Y-%m-%d %H:%M"))
    out = section(text, t("wiki_changes.changed_heading")) + section(text, t("wiki_changes.linked_heading"))
    flagged = section(text, t("wiki_changes.flagged_heading"))
    out += flagged[:MAX_FLAGGED]
    if len(flagged) > MAX_FLAGGED:
        out.append(t("morning_briefing.more", n=len(flagged) - MAX_FLAGGED, file="_Agent-Context/WIKI-CHANGES.md"))
    return out + section(text, t("concept_review.waiting_heading")), ""


def revisit(today: datetime) -> list[str]:
    text = read("RESURFACE.md")
    when = stamp(text)
    if not when or today - when > timedelta(days=RESURFACE_DAYS):
        return []
    return [ln for ln in text.splitlines() if ln.startswith("- ")][:5]


def drift() -> list[str]:
    text = read("CONTEXT-DRIFT.md")
    bullets = [ln for ln in text.splitlines() if ln.startswith("- ")]
    return bullets[:5] + [t("morning_briefing.drift_question")] if bullets else []


def research(today: datetime) -> str:
    """Monday (and Wednesday, Friday while open): last week's research follow-up."""
    if today.weekday() not in (0, 2, 4):
        return ""
    try:
        entry = json.loads((VAULT / ".agents/state/research_followup.json").read_text(encoding="utf-8"))["open"][0]
    except (OSError, ValueError, KeyError, IndexError):
        return ""
    kind = entry.get("kind", "report")
    if kind == "no_epic":
        return t("morning_briefing.research_no_epic") if today.weekday() == 0 else ""
    key = "research_recurrence" if kind == "recurrence" else "research_open"
    return t(f"morning_briefing.{key}", question=entry.get("question", ""), report=entry.get("report") or "")


def actions(red: bool, health_lines: list[str], promise_lines: list[str]) -> list[str]:
    out = []
    if red:
        out += [ln[2:] for ln in health_lines if ln.startswith("- 🔴")][:2]
    import kill_criteria
    due = t("kill_criteria.due_heading", days=kill_criteria.DUE_SOON_DAYS, n=0)
    out += [ln[2:] for ln in section(read("KILL-CRITERIA.md"), due) if ln.startswith("- ")][:2]
    conflict = t("wiki_changes.flag_conflict", page="", text="").split(",")[0]     # "- Çelişki"
    out += [ln[2:] for ln in section(read("WIKI-CHANGES.md"), t("wiki_changes.flagged_heading"))
            if ln.startswith(conflict)][:1]
    out += [ln.replace("- [ ] ", "", 1) for ln in promise_lines if ln.startswith("- [ ]")]
    return [f"{n}. {ln}" for n, ln in enumerate(out[:5], 1)]


# ─── The block and the file ──────────────────────────────────────

def build(now: datetime | None = None) -> str:
    now = now or datetime.now()
    h, red = health()
    crm_lines, crm_note = crm(now)
    wiki_lines, wiki_note = wiki(now)
    h += [ln for ln in (crm_note, wiki_note) if ln]
    p = promises(now)
    parts = [(t("morning_briefing.h_health"), h),
             (t("morning_briefing.h_calendar"), calendar(now)),
             (t("morning_briefing.h_mail"), [t("morning_briefing.mail_note")]),
             (t("morning_briefing.h_promises", days=PROMISE_DAYS), p),
             (t("morning_briefing.h_crm"), crm_lines),
             (t("morning_briefing.h_wiki"), wiki_lines),
             (t("morning_briefing.h_revisit"), revisit(now)),
             (t("morning_briefing.h_research"), [research(now)] if research(now) else []),
             (t("morning_briefing.h_drift"), drift()),
             (t("morning_briefing.h_actions"), actions(red, h, p))]
    lines = [START, t("morning_briefing.title", date=now.strftime("%Y-%m-%d")), ""]
    for head, body in parts:
        if body:
            lines += [head, *body, ""]
    lines += [t("morning_briefing.made", time=now.strftime("%Y-%m-%d %H:%M")), END]
    return "\n".join(lines)


def merge(existing: str, block: str) -> str:
    """Put the block at the top: under the frontmatter, above everything else.
    A block already there is replaced, so a rerun never stacks a second one."""
    if START in existing and END in existing:
        return re.sub(re.escape(START) + r".*?" + re.escape(END), lambda _: block, existing, count=1, flags=re.S)
    fm = re.match(r"\A---\n.*?\n---\n", existing, re.S)
    head, rest = (fm.group(0), existing[fm.end():]) if fm else ("", existing)
    return head + block + "\n\n" + rest.lstrip("\n") if rest.strip() else head + block + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="print the block, write nothing")
    ap.add_argument("--force", action="store_true", help="also on a weekend")
    args = ap.parse_args(argv)
    now = datetime.now()
    if now.weekday() >= 5 and not args.force:
        print("RUNLOG briefing=0")
        return 0
    block = build(now)
    if args.dry_run:
        print(block)
        return 0
    path = OUT_DIR / f"daily-briefing-{now:%Y-%m-%d}.md"
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write(path, merge(old, block))
    print(f"[ok] {path.relative_to(VAULT)}")
    print("RUNLOG briefing=1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
