#!/usr/bin/env python3
"""kill_criteria.py: the quit rule, on rails.

Every project notes.md may carry a "## Kill Criteria" section with lines of the form

    - [ ] YYYY-MM-DD | condition | consequence

meaning "if on that date the condition holds, apply the consequence" (stop,
re-scope, escalate, archive). Annie Duke's point in Quit: decide the quit
condition in advance, with a date, because in the moment you never will.

This module reads those lines from every Work/ and Personal/ project (same
discovery as build_dashboard.py), classifies them, and writes
_Agent-Context/KILL-CRITERIA.md:
  - breached : date passed, box still open  -> red in the briefing
  - due      : date within DUE_SOON_DAYS
  - missing  : active or on-hold projects with no open criterion at all
Deterministic, no LLM. Runs hourly from health_check.py; build_dashboard.py
renders the same scan into the dashboard. `python3 tools/kill_criteria.py`
writes the file, `--dry-run` only prints.
"""
import argparse
import os
import re
import sys
from datetime import date, datetime

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
sys.path.insert(0, os.path.join(VAULT, "tools"))
from i18n import t  # noqa: E402
import build_dashboard as bd  # noqa: E402  (project discovery, frontmatter)

OUT_FILE = os.path.join(VAULT, "_Agent-Context", "KILL-CRITERIA.md")
DUE_SOON_DAYS = 14
SKIP_STATUSES = ("archiv", "wind", "done", "closed")
LINE_RE = re.compile(r"^\s*-\s*\[( |x|X)\]\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([^|]+?)\s*(?:\|\s*(.+?))?\s*$")


def criteria_lines(text):
    """(checked, date, condition, consequence) for every parsable line of the section."""
    body = bd.section(text, "Kill Criteria")
    out = []
    for line in body.splitlines():
        m = LINE_RE.match(line)
        if not m:
            continue
        try:
            d = datetime.strptime(m.group(2), "%Y-%m-%d").date()
        except ValueError:
            continue
        out.append((m.group(1).lower() == "x", d, m.group(3).strip(), (m.group(4) or "").strip()))
    return out


def scan(today=None):
    today = today or date.today()
    res = {"breached": [], "due": [], "upcoming": 0, "done": 0, "missing": []}
    for cat, name, path in bd.project_notes():
        text = path.read_text(errors="ignore")
        status = bd.fm(text).get("status", "active").lower()
        if any(k in status for k in SKIP_STATUSES):
            continue
        rows = criteria_lines(text)
        open_rows = [r for r in rows if not r[0]]
        res["done"] += len(rows) - len(open_rows)
        if not open_rows:
            res["missing"].append((cat, name, status))
            continue
        for _, d, cond, cons in open_rows:
            delta = (d - today).days
            item = {"project": name, "cat": cat, "date": d, "days": delta, "condition": cond, "consequence": cons}
            if delta < 0:
                res["breached"].append(item)
            elif delta <= DUE_SOON_DAYS:
                res["due"].append(item)
            else:
                res["upcoming"] += 1
    res["breached"].sort(key=lambda i: i["date"])
    res["due"].sort(key=lambda i: i["date"])
    return res


def _cons(item):
    return (t("kill_criteria.consequence_sep") + item["consequence"]) if item["consequence"] else ""


def render(res, now=None):
    now = now or datetime.now()
    L = [t("kill_criteria.title"), "", t("kill_criteria.intro", time=now.strftime("%Y-%m-%d %H:%M")), ""]
    L.append(t("kill_criteria.breached_heading", n=len(res["breached"])))
    for i in res["breached"]:
        L.append(t("kill_criteria.breached_row", project=i["project"], date=i["date"].isoformat(),
                   condition=i["condition"], consequence=_cons(i), days=-i["days"]))
    if not res["breached"]:
        L.append(t("kill_criteria.none_breached"))
    L += ["", t("kill_criteria.due_heading", days=DUE_SOON_DAYS, n=len(res["due"]))]
    for i in res["due"]:
        L.append(t("kill_criteria.due_row", project=i["project"], date=i["date"].isoformat(),
                   condition=i["condition"], consequence=_cons(i), left=i["days"]))
    if not res["due"]:
        L.append(t("kill_criteria.none_due"))
    L += ["", t("kill_criteria.missing_heading", n=len(res["missing"]))]
    for cat, name, status in res["missing"]:
        L.append(t("kill_criteria.missing_row", cat=cat, project=name, status=status))
    if not res["missing"]:
        L.append(t("kill_criteria.none_missing"))
    L += ["", t("kill_criteria.upcoming_line", n=res["upcoming"], done=res["done"]), "", t("kill_criteria.footer"), ""]
    return "\n".join(L)


def write(res=None):
    res = res or scan()
    text = render(res)
    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, "w") as fh:
        fh.write(text)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print instead of writing KILL-CRITERIA.md")
    args = ap.parse_args()
    res = scan()
    if args.dry_run:
        print(render(res))
    else:
        write(res)
        print(f"wrote {os.path.relpath(OUT_FILE, VAULT)}: {len(res['breached'])} breached, "
              f"{len(res['due'])} due, {len(res['missing'])} projects without criteria")


if __name__ == "__main__":
    main()
