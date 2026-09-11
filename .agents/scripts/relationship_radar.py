#!/usr/bin/env python3
"""Relationship radar (worker, weekly).

Plan 3: a signal/alert layer on top of the person dossiers (Plan 1). No LLM,
deterministic: computes last contact + cadence + score momentum from Spiky
meetings and reciprocity (whom you owe / who owes you) from TASKS.md; on a
deviation it drops a weekly radar to Telegram and writes .wiki/relationships/radar.md.

Thresholds (owner, 2026-08-28): silence 20 weeks = yellow, 32 weeks = red;
a momentum drop of 15+ points is flagged. Scope: person rows in entities.md + scope label.
"""
import os
import re
import statistics
import sys
from datetime import datetime

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
sys.path.insert(0, os.path.join(VAULT, ".agents", "scripts"))
sys.path.insert(0, os.path.join(VAULT, "tools"))
from watchdog import send_telegram
from owner_profile import LANG  # noqa: E402
from i18n import t, t_list  # noqa: E402

SPIKY_DIR = os.path.join(VAULT, "Inbox", "Spiky")
TASKS_FILE = os.path.join(VAULT, "_Agent-Context", "TASKS.md")
REGISTRY = os.path.join(VAULT, "_Agent-Context", "entities.md")
OUT_FILE = os.path.join(VAULT, ".wiki", "relationships", "radar.md")

STALE_YELLOW_WEEKS = 20
STALE_RED_WEEKS = 32
MOMENTUM_DROP = 15
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
SCORE_LABELS = ("Spiky Score", "Attention Score", "Interaction Score", "Emotion Score")


def read(path):
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return ""


def load_people():
    """entities.md -> person records: {name, terms, scope}."""
    people = []
    for line in read(REGISTRY).splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "|" not in s:
            continue
        parts = [p.strip() for p in s.split("|")]
        if len(parts) < 2 or parts[1] != "person":
            continue
        name = parts[0]
        aliases = [a.strip() for a in (parts[2].split(",") if len(parts) > 2 else []) if a.strip()]
        scope = parts[3] if len(parts) > 3 and parts[3] else "other"
        people.append({"name": name, "terms": [name] + aliases, "scope": scope})
    return people


def parse_score(text):
    """Take the first number from the 'Spiky Score' block in the note body -> int | None."""
    lines = text.splitlines()
    for i, l in enumerate(lines):
        if l.strip() == "Spiky Score":
            for j in range(i + 1, min(i + 6, len(lines))):
                if re.fullmatch(r"\d{1,3}", lines[j].strip()):
                    return int(lines[j].strip())
    return None


def participants_line(text):
    lines = text.splitlines()
    for i, l in enumerate(lines):
        if l.strip() == "Participants":
            for j in range(i + 1, min(i + 4, len(lines))):
                if lines[j].strip():
                    return lines[j]
    return ""


def meetings_for(person):
    """Spiky meetings the person attended: [(date, score, fname)] chronological."""
    out = []
    if not os.path.isdir(SPIKY_DIR):
        return out
    low_terms = [t.casefold() for t in person["terms"]]
    for f in os.listdir(SPIKY_DIR):
        if not f.endswith(".md"):
            continue
        text = read(os.path.join(SPIKY_DIR, f))
        pline = participants_line(text).casefold()
        if not any(t in pline for t in low_terms):
            continue
        m = DATE_RE.match(f)
        if not m:
            continue
        try:
            d = datetime.strptime(m.group(1), "%Y-%m-%d")
        except ValueError:
            continue
        out.append((d, parse_score(text), f))
    out.sort(key=lambda x: x[0])
    return out


def tasks_for(person):
    """(waiting_on, you_owe) TASKS.md rows.

    Section headings are matched in the current language and in English
    (ledgers written by either an existing or a fresh install)."""
    waiting, owe = [], []
    section = None
    low_terms = [x.casefold() for x in person["terms"]]
    waiting_names = t_list("relationship_radar.tasks_section_waiting")
    promise_names = t_list("relationship_radar.tasks_section_promises")
    for line in read(TASKS_FILE).splitlines():
        s = line.strip()
        if s.startswith("## "):
            section = s[3:].strip()
            continue
        m = re.match(r"- \[ \] (.+)", s)
        if not m:
            continue
        row = m.group(1)
        if section in waiting_names and any(row.casefold().startswith(x) for x in low_terms):
            waiting.append(row)
        elif section in promise_names and any(x in row.casefold() for x in low_terms):
            owe.append(row)
    return waiting, owe


def weeks_since(d):
    return (datetime.now() - d).days / 7.0


def analyze():
    rows = []
    for p in load_people():
        mtgs = meetings_for(p)
        if not mtgs:
            continue
        dates = [d for d, _, _ in mtgs]
        last = dates[-1]
        stale_w = weeks_since(last)
        # cadence: median days between consecutive meetings
        gaps = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
        cadence_d = statistics.median(gaps) if gaps else None
        # momentum: scored meetings; last score vs the mean of the earlier ones
        scores = [s for _, s, _ in mtgs if s is not None]
        drop = None
        if len(scores) >= 2:
            prior_avg = statistics.mean(scores[:-1])
            if scores[-1] <= prior_avg - MOMENTUM_DROP:
                drop = round(prior_avg - scores[-1])
        waiting, owe = tasks_for(p)
        rows.append({
            "name": p["name"], "scope": p["scope"], "terms": p["terms"],
            "last": last, "stale_w": stale_w,
            "cadence_d": cadence_d, "last_score": scores[-1] if scores else None,
            "drop": drop, "waiting": waiting, "owe": owe, "n": len(mtgs),
        })
    return rows


def attendee_signals(attendee_strings):
    """For meeting_brief: relationship signals of the people matching the attendee names.
    English prompt material, not user-facing text.
    -> ['Name: last contact X weeks ago, cadence ~Y days, last score Z, you are waiting on N items from them']"""
    low = [a.casefold() for a in attendee_strings if a]
    if not low:
        return []
    out = []
    for r in analyze():
        if not any(x.casefold() in a for a in low for x in r["terms"]):
            continue
        parts = [f"last contact {int(r['stale_w'])} weeks ago"]
        if r["cadence_d"]:
            parts.append(f"usual cadence ~{int(r['cadence_d'])} days")
        if r["last_score"] is not None:
            parts.append(f"last meeting score {r['last_score']}")
        if r["drop"]:
            parts.append(f"MOMENTUM LOW ({r['drop']} points)")
        if r["waiting"]:
            parts.append(f"you are WAITING ON {len(r['waiting'])} open item(s) FROM THEM")
        if r["owe"]:
            parts.append(f"you OWE THEM {len(r['owe'])} item(s)")
        out.append(f"{r['name']} ({r['scope']}): " + ", ".join(parts))
    return out


def build_report(rows):
    red = [r for r in rows if r["stale_w"] >= STALE_RED_WEEKS]
    yellow = [r for r in rows if STALE_YELLOW_WEEKS <= r["stale_w"] < STALE_RED_WEEKS]
    momentum = [r for r in rows if r["drop"]]
    waiting = [r for r in rows if r["waiting"]]

    def fmt(r):
        return t("relationship_radar.row_contact", name=r["name"], scope=r["scope"], weeks=int(r["stale_w"]))

    msg = []
    if red:
        msg.append(t("relationship_radar.report_red", weeks=STALE_RED_WEEKS))
        msg += [f"- {fmt(r)}" for r in sorted(red, key=lambda r: -r["stale_w"])]
    if yellow:
        msg.append(t("relationship_radar.report_yellow", weeks=STALE_YELLOW_WEEKS))
        msg += [f"- {fmt(r)}" for r in sorted(yellow, key=lambda r: -r["stale_w"])]
    if momentum:
        msg.append(t("relationship_radar.report_momentum", points=MOMENTUM_DROP))
        msg += ["- " + t("relationship_radar.row_momentum", name=r["name"], score=r["last_score"], drop=r["drop"])
                for r in momentum]
    if waiting:
        msg.append(t("relationship_radar.report_waiting"))
        for r in waiting:
            msg.append("- " + t("relationship_radar.row_waiting", name=r["name"], n=len(r["waiting"])))
    return "\n".join(msg) if msg else ""


def write_snapshot(rows):
    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    week = t("relationship_radar.week_short")
    lines = ["---", f"lang: {LANG}",
             "summary_en: Deterministic relationship radar: per-person last contact, cadence, "
             "Spiky score momentum, and open reciprocity from meeting corpus + TASKS.md.",
             f"compiled_at: {datetime.now().isoformat(timespec='seconds')}",
             "type: relationships", "---", t("relationship_radar.snapshot_title"), "",
             t("relationship_radar.snapshot_intro", updated=datetime.now().strftime('%Y-%m-%d %H:%M'),
               yellow=STALE_YELLOW_WEEKS, red=STALE_RED_WEEKS, points=MOMENTUM_DROP), "",
             t("relationship_radar.snapshot_table_header"),
             "|---|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: -r["stale_w"]):
        cad = int(r["cadence_d"]) if r["cadence_d"] else "-"
        mom = f"-{r['drop']}" if r["drop"] else "-"
        lines.append(f"| [[{r['name']}]] | {r['scope']} | {r['last'].strftime('%Y-%m-%d')} "
                     f"({int(r['stale_w'])}{week}) | {cad} | {r['last_score'] or '-'} | {mom} "
                     f"| {len(r['waiting'])} | {len(r['owe'])} |")
    with open(OUT_FILE, "w") as fh:
        fh.write("\n".join(lines) + "\n")


def main():
    rows = analyze()
    if not rows:
        print("radar: no data")
        return
    write_snapshot(rows)
    report = build_report(rows)
    if report:
        send_telegram(t("relationship_radar.telegram_title") + "\n\n" + report +
                      "\n\n" + t("relationship_radar.telegram_detail"))
        print(f"radar sent ({len(rows)} people analysed)")
    else:
        print(f"radar clean ({len(rows)} people, no flags)")


if __name__ == "__main__":
    main()
