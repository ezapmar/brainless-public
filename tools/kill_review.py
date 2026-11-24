#!/usr/bin/env python3
"""kill_review.py: the overdue kill criteria as one weekly message, answered with one reply.

kill_criteria.py finds the criteria whose date passed with the box still open
and health_check shows the count. A count asks for nothing, though: on
19/11/2026 seventeen criteria were overdue, the oldest by 35 days, and each one
needed the owner to open a note and edit a line. This tool asks instead.

  send     Sunday night (night.sh): the overdue criteria, oldest first, at most
           MAX_ITEMS, numbered, in one #projects message. Projects with no
           criterion at all are named at the end.
  reply    the owner answers that message once, one clause per decision:
             3 kapat            tick the box (the criterion is settled)
             5 arşivle          tick the box, set status: archived
             7 beklet           tick the box, set status: on-hold
             2 ertele 15 Aralık move the date (also 2026-12-15, 15.12, 2 hafta)
           "1 4 kapat, 6 ertele 3 hafta" works. There is no "all" form.

A reply is the owner's explicit approval, and it is the only thing that writes:
the criterion line and the frontmatter status in that project's notes.md, plus
one dated line in its Log. The line is matched by its date and condition as
they were when the message went out; a line edited since is reported, not
guessed at. Deterministic, no LLM.

Usage:
  python3 tools/kill_review.py              send this week's message
  python3 tools/kill_review.py --dry-run    print it, send nothing
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from fsutil import atomic_write
from i18n import t, t_list
from paths import vault_root
from vault_lock import lock_exclusive

VAULT = Path(vault_root())
STATE = VAULT / ".agents" / "state" / "kill_review.json"
CHANNEL = IDENTITY = "projects"
MAX_ITEMS = 10
KEEP_DAYS = 60
ACTIONS = ("close", "archive", "hold", "defer")
STATUS = {"archive": "archived", "hold": "on-hold"}
_MONTHS = {m: i for i, names in enumerate((
    ("ocak", "january", "jan"), ("şubat", "subat", "february", "feb"), ("mart", "march", "mar"),
    ("nisan", "april", "apr"), ("mayıs", "mayis", "may"), ("haziran", "june", "jun"),
    ("temmuz", "july", "jul"), ("ağustos", "agustos", "august", "aug"),
    ("eylül", "eylul", "september", "sep"), ("ekim", "october", "oct"),
    ("kasım", "kasim", "november", "nov"), ("aralık", "aralik", "december", "dec")), 1) for m in names}
_UNITS = {"gün": 1, "gun": 1, "day": 1, "days": 1, "hafta": 7, "week": 7, "weeks": 7,
          "ay": 30, "month": 30, "months": 30}


# ─── What is overdue ─────────────────────────────────────────────

def overdue(today: date | None = None) -> tuple[list[dict], list[str]]:
    """(overdue criteria oldest first, names of projects with no open criterion)."""
    import build_dashboard as bd
    import kill_criteria as kc
    today = today or date.today()
    items, missing = [], []
    for _cat, name, path in bd.project_notes():
        text = path.read_text(errors="ignore", encoding="utf-8")
        if any(k in bd.fm(text).get("status", "active").lower() for k in kc.SKIP_STATUSES):
            continue
        rows = [r for r in kc.criteria_lines(text) if not r[0]]
        if not rows:
            missing.append(name)
        for _, d, cond, cons in rows:
            if d < today:
                items.append({"project": name, "path": str(Path(path).relative_to(VAULT)),
                              "date": d.isoformat(), "condition": cond, "consequence": cons,
                              "days": (today - d).days})
    items.sort(key=lambda i: (i["date"], i["project"]))
    return items, missing


def message(items: list[dict], total: int, missing: list[str]) -> str:
    lines = [t("kill_review.title", n=total), ""]
    for i in items:
        lines.append(t("kill_review.row", no=i["no"], project=i["project"], days=i["days"],
                       condition=i["condition"]))
        if i["consequence"]:
            lines.append("   " + t("kill_review.consequence", consequence=i["consequence"]))
        lines.append("")
    if total > len(items):
        lines += [t("kill_review.more", n=total - len(items), max=MAX_ITEMS), ""]
    if missing:
        lines += [t("kill_review.missing", names=", ".join(missing)), ""]
    return "\n".join(lines + [t("kill_review.footer")])


# ─── Reply grammar ───────────────────────────────────────────────

def _words() -> dict[str, str]:
    return {w.casefold(): a for a in ACTIONS for lang in ("tr", "en")
            for w in t_list(f"kill_review.{a}_words", lang)}


def parse_date(arg: str, today: date) -> date | None:
    """A date the owner typed after "ertele": ISO, 15.12[.2026], "15 Aralık", "2 hafta"."""
    arg = arg.strip().casefold()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", arg)
    parts = None
    if m:
        parts = (int(m[1]), int(m[2]), int(m[3]))
    elif m := re.fullmatch(r"(\d{1,2})[./](\d{1,2})(?:[./](\d{4}))?", arg):
        parts = (int(m[3]) if m[3] else None, int(m[2]), int(m[1]))
    elif (m := re.fullmatch(r"(\d{1,2})\s+([^\W\d_]+)(?:\s+(\d{4}))?", arg)) and m[2] in _MONTHS:
        parts = (int(m[3]) if m[3] else None, _MONTHS[m[2]], int(m[1]))
    elif (m := re.fullmatch(r"(\d{1,3})\s+([^\W\d_]+)", arg)) and m[2] in _UNITS:
        return today + timedelta(days=int(m[1]) * _UNITS[m[2]])
    if not parts:
        return None
    year, month, day = parts
    try:
        if year is None:                      # no year typed: the next time that day comes
            d = date(today.year, month, day)
            return d if d > today else date(today.year + 1, month, day)
        return date(year, month, day)
    except ValueError:
        return None


def parse_reply(text: str, numbers: set[int], today: date) -> tuple[dict[int, tuple], list[str]]:
    """({number: (action, new date or None)}, clauses that could not be read)."""
    words, out, bad = _words(), {}, []
    for clause in re.split(r"[,;\n]+", text):
        clause = clause.strip().rstrip(".!")
        if not clause:
            continue
        toks = clause.split()
        at = next((i for i, tok in enumerate(toks) if tok.casefold() in words), None)
        nums = [int(x) for x in toks[:at or 0] if x.isdigit()]
        if at is None or not nums or len(nums) != at or any(n not in numbers for n in nums):
            bad.append(clause)
            continue
        action, when = words[toks[at].casefold()], None
        if action == "defer":
            when = parse_date(" ".join(toks[at + 1:]), today)
            if not when or when <= today:
                bad.append(clause)
                continue
        elif toks[at + 1:]:
            bad.append(clause)
            continue
        out.update({n: (action, when) for n in nums})
    return out, bad


# ─── The one place that writes a project note ────────────────────

def _log(text: str, line: str, today: date) -> str:
    """Append one bullet to the note's Log under today's date."""
    head = f"### {today.isoformat()}"
    m = re.search(r"^## Log[ \t]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m:
        return text.rstrip("\n") + f"\n\n## Log\n{head}\n- {line}\n"
    body = m.group(1).rstrip("\n")
    last = re.findall(r"^### .*$", body, re.M)
    add = f"\n- {line}\n" if last and last[-1].strip() == head else f"\n{head}\n- {line}\n"
    tail = text[m.end():]
    return text[:m.start(1)] + body + add + ("\n" if tail else "") + tail


def apply(item: dict, action: str, when: date | None, today: date | None = None) -> str:
    """Carry out one decision in the project's notes.md. Returns 'ok' or why not."""
    today = today or date.today()
    path = VAULT / item["path"]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "missing"
    pat = re.compile(r"^(\s*-\s*\[) (\]\s*)" + re.escape(item["date"]) + r"(\s*\|\s*"
                     + re.escape(item["condition"]) + r".*)$", re.M)
    hits = pat.findall(text)
    if len(hits) != 1:
        return "changed"                      # edited, ticked or duplicated since the message
    if action == "defer":
        text = pat.sub(lambda m: f"{m[1]} {m[2]}{when.isoformat()}{m[3]}", text)
        note = t("kill_review.log_defer", old=item["date"], new=when.isoformat(), condition=item["condition"])
    else:
        text = pat.sub(lambda m: f"{m[1]}x{m[2]}{item['date']}{m[3]}", text)
        note = t(f"kill_review.log_{action}", condition=item["condition"])
        if action in STATUS:
            fm = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
            if fm:
                block = re.sub(r"(?m)^status:.*$", f"status: {STATUS[action]}", fm.group(1), count=1)
                block = re.sub(r"\bstatus/[\w-]+", f"status/{STATUS[action]}", block)
                text = "---\n" + block + "\n---\n" + text[fm.end():]
    atomic_write(path, _log(text, note, today))
    return "ok"


# ─── State, send, reply ──────────────────────────────────────────

@contextmanager
def locked():
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with STATE.with_suffix(".lock").open("a") as lock:
        lock_exclusive(lock)
        state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
        state.setdefault("batches", [])
        state.setdefault("handled", [])
        yield state
        atomic_write(STATE, json.dumps(state, ensure_ascii=False, indent=1) + "\n")


def send(dry: bool = False, box=None, today: date | None = None) -> dict:
    today = today or date.today()
    items, missing = overdue(today)
    counts = {"overdue": len(items), "asked": 0}
    if not items:
        print("RUNLOG overdue=0 asked=0")
        return counts
    asked = [dict(i, no=n, status="pending") for n, i in enumerate(items[:MAX_ITEMS], 1)]
    body = message(asked, len(items), missing)
    print(("[dry] " if dry else "") + body.replace("\n", "\n  "))
    if not dry:
        from notify import Outbox
        box = box or Outbox()
        key = "kill:batch:" + hashlib.sha256(f"{body}\0{today}".encode()).hexdigest()[:20]
        box.enqueue(IDENTITY, CHANNEL, body, key=key)
        box.deliver(key)
        rec = box.record(key)
        with locked() as state:
            cutoff = time.time() - KEEP_DAYS * 86400
            state["batches"] = [b for b in state["batches"] if b["created"] > cutoff]
            state["batches"].append({"key": key, "event": rec["event"] if rec else None,
                                     "created": time.time(), "items": asked})
        counts["asked"] = len(asked)
    print(f"RUNLOG overdue={counts['overdue']} asked={counts['asked']}")
    return counts


def handle(msg, channel, owner, *, text=None, box=None, today: date | None = None):
    """Apply the decisions in a reply to a kill review message. False when the
    message answers something else, so the generic reply worker takes it."""
    from notify import Outbox, event_id
    from today_reply import direct_parent
    box = box or Outbox()
    if msg.get("pubkey") != owner or channel != box.client.channel(CHANNEL, IDENTITY):
        return False
    mid, parent = event_id(msg), direct_parent(msg)
    if not mid or not parent:
        return False
    today = today or date.today()
    with locked() as state:
        for b in state["batches"]:
            if not b.get("event"):
                rec = box.record(b["key"])
                b["event"] = rec["event"] if rec else None
        batch = next((b for b in state["batches"] if b.get("event") == parent), None)
        if not batch:
            return False
        if mid in state["handled"]:
            return True
        open_ = {i["no"]: i for i in batch["items"] if i["status"] == "pending"}
        reply = msg.get("content", "") if text is None else text
        choices, bad = parse_reply(reply, set(open_), today)
        lines = []
        for no in sorted(choices):
            action, when = choices[no]
            item = open_[no]
            result = apply(item, action, when, today)
            if result == "ok":
                item["status"] = action
                lines.append(t(f"kill_review.done_{action}", no=no, project=item["project"],
                               date=when.isoformat() if when else ""))
            else:
                lines.append(t(f"kill_review.failed_{result}", no=no, project=item["project"]))
        lines += [t("kill_review.unread", clause=c) for c in bad]
        if not lines:
            lines = [t("kill_review.help")]
        left = sorted(n for n, i in open_.items() if i["status"] == "pending")
        if left and choices:
            lines.append(t("kill_review.left", nums=", ".join(map(str, left))))
        key = "kill:reply:" + mid
        box.enqueue(IDENTITY, CHANNEL, "\n".join(lines), key=key, parent=mid)
        box.deliver(key)
        state["handled"] = (state["handled"] + [mid])[-500:]
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="print the message, send nothing")
    args = ap.parse_args(argv)
    send(dry=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
