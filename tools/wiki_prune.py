#!/usr/bin/env python3
"""Count the pile, then drain it. No model is called here.

The owner's worry, in his words (voice note, 2026-09-18): the vault "looks good
on paper, but after a while risks becoming a know-it-all that talks about
everything and has no core". The belief that underwrites capture, "capture
everything, filter later", names its own failure: the number of Inbox files
older than 14 days should stay near zero. On 2026-09-19 it was 125, up from
121 at the 4 September audit. Filter later had become filter never.

So this script does two things, in the order the three layers need them.

  --count    The second layer, think clearly, had no output anyone could
             count, so it could never visibly fail. This writes the counts to
             _Agent-Context/PILE-SCORECARD.md: captures per graded decision,
             query files per decision, stale Inbox files, orphan wiki pages,
             the bridge ratio between homes, decisions and challenged beliefs
             in the window, and what was archived. For the first four weeks
             the numbers are the whole point; ceilings come from the data
             afterwards, the way editor_lint.py calibrates against the
             owner's own corpus rather than against a guess.

  --archive  Mechanical rules, each with a reason, in the manner of
             weekly_research.filter_claims(): a page that nothing has linked
             to in a month or two, and that no job would rebuild, moves to
             .wiki/_archive/. Nothing is deleted; git keeps the history and
             LOG.md keeps the reasons. Dry run by default; --apply moves.

The rules never touch a human home except one, approved on 2026-09-19: a
Spiky meeting report that has been summarised, mined for tasks and left in
Inbox/ for two weeks moves to Archive/Spiky/. That is the CLAUDE.md map, "a
processed file leaves Inbox", finally executed. Everything else in the human
homes is listed under "Pending" in the scorecard and left where it is.

Every move is written twice: .agents/state/prune_log.jsonl for machines and
.wiki/_archive/LOG.md for the owner, with the rule and the reason. To undo
one, git mv the file back and mark the row.

  python3 tools/wiki_prune.py                        # --count
  python3 tools/wiki_prune.py --archive              # list what would move
  python3 tools/wiki_prune.py --archive --apply      # move it, log it
  python3 tools/wiki_prune.py --archive --only spiky --apply
"""
import argparse
import json
import os
import re
import shutil
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from i18n import t  # noqa: E402
from lint_wiki import (VAULT, WIKI, all_wiki_files, link_graph, orphan_pages,  # noqa: E402
                       parse_fm)

ARCHIVE = WIKI / "_archive"
QUERIES = WIKI / "digests" / "queries"
SUMMARIES = WIKI / "summaries"
INBOX = VAULT / "Inbox"
SPIKY = INBOX / "Spiky"
SPIKY_ARCHIVE = VAULT / "Archive" / "Spiky"
DECISIONS = VAULT / "Thinking" / "Decisions"
BELIEFS = VAULT / "Thinking" / "Beliefs"
IDEAS = VAULT / "Thinking" / "Ideas"
CAPTURE_DIRS = (VAULT / "Archive" / "Daily-Captures", VAULT / "Thinking" / "Daily")
TASKS = VAULT / "_Agent-Context" / "TASKS.md"
SCORECARD_MD = VAULT / "_Agent-Context" / "PILE-SCORECARD.md"
STATE_DIR = VAULT / ".agents" / "state"
PRUNE_LOG = STATE_DIR / "prune_log.jsonl"
SPIKY_DONE = STATE_DIR / "spiky_actions_done"
LOG_MD = ARCHIVE / "LOG.md"
README_MD = ARCHIVE / "README.md"

# Filed analyses that carry hypotheses graded later never age out on their own.
QUERY_KEEP = ("research", "decide", "dialectic")
QUERY_AGE_DAYS = 30
SUMMARY_AGE_DAYS = 60
ORPHAN_AGE_DAYS = 60
INBOX_STALE_DAYS = 14      # the belief's own criterion
INBOX_STALE_RED = 10       # above this the health check goes red
WINDOW_DAYS = 30
TASK_STALE_DAYS = 60
SEED_LONELY_DAYS = 30
SCORECARD_ROWS = 20
GRAPH_ROWS = 26          # weeks of graph-health history (tools/wiki_metrics.py)
RULES = ("query", "summary", "orphan", "spiky")

_DATE = re.compile(r"(20\d\d-\d\d-\d\d)")


class Move(NamedTuple):
    rule: str
    src: Path
    dst: Path
    reason: str
    inbound: int
    age: int


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def read(path):
    try:
        return Path(path).read_text(errors="replace")
    except OSError:
        return ""


def rel(path):
    try:
        return str(Path(path).relative_to(VAULT))
    except ValueError:
        return str(path)


def nfc(s):
    return unicodedata.normalize("NFC", s or "")


def no_dashes(text):
    """House rule: no em or en dashes anywhere in output."""
    return (text or "").replace("\u2014", " - ").replace("\u2013", "-").replace("  - ", " - ")


def page_date(p: Path, fm=None) -> datetime:
    """When a page was made, best evidence first: the frontmatter stamp, the
    date in the file name, the mtime. Two machines share this vault through
    git and a pull rewrites mtimes, so the name beats the clock."""
    fm = fm if fm is not None else parse_fm(read(p))
    for key in ("compiled_at", "date", "created"):
        m = _DATE.search(fm.get(key, "") or "")
        if m:
            return datetime.strptime(m.group(1), "%Y-%m-%d")
    m = _DATE.search(p.name)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d")
        except ValueError:
            pass
    try:
        return datetime.fromtimestamp(p.stat().st_mtime)
    except OSError:
        return datetime.now()


def age_days(p: Path, now: datetime, fm=None) -> int:
    return (now - page_date(p, fm)).days


def summary_index():
    """source path -> summary page, NFC on both sides: macOS hands the
    compiler NFD file names and the frontmatter carries whatever it got."""
    out = {}
    if SUMMARIES.exists():
        for s in SUMMARIES.glob("*.md"):
            src = parse_fm(read(s)).get("source", "")
            if src:
                out[nfc(src)] = s
    return out


# --------------------------------------------------------------------------
# Archive rules
# --------------------------------------------------------------------------

def rule_query(graph, now):
    for q in sorted(QUERIES.glob("*.md")) if QUERIES.exists() else []:
        fm = parse_fm(read(q))
        cmd = fm.get("command", "")
        if cmd in QUERY_KEEP:
            continue
        age = age_days(q, now, fm)
        if age <= QUERY_AGE_DAYS or graph["inbound"].get(q, 0) > 0:
            continue
        yield Move("query", q, ARCHIVE / "queries" / q.name,
                   t("wiki_prune.reason_query", cmd=cmd or "?", days=age), 0, age)


def rule_summary(graph, now):
    """A summary whose source left the compiled roots (moved to Archive/, or
    gone) and that nothing links to. A summary of a live source is left alone
    even when orphaned: the compiler would only rebuild it the next night."""
    for s in sorted(SUMMARIES.glob("*.md")) if SUMMARIES.exists() else []:
        fm = parse_fm(read(s))
        src = fm.get("source", "")
        if not src:
            continue
        gone = src.startswith("Archive/") or not (VAULT / src).exists()
        if not gone:
            continue
        age = age_days(s, now, fm)
        if age <= SUMMARY_AGE_DAYS or graph["inbound"].get(s, 0) > 0:
            continue
        yield Move("summary", s, ARCHIVE / "summaries" / s.name,
                   t("wiki_prune.reason_summary", days=age), 0, age)


def rule_orphan(graph, now, files, taken):
    for p in orphan_pages(files, graph):
        if p in taken or "_lint-report" in p.name:
            continue
        fm = parse_fm(read(p))
        if p.parent == SUMMARIES:
            src = fm.get("source", "")
            if src and (VAULT / src).exists() and not src.startswith("Archive/"):
                continue  # the compiler would bring it straight back
        age = age_days(p, now, fm)
        if age <= ORPHAN_AGE_DAYS:
            continue
        yield Move("orphan", p, ARCHIVE / p.relative_to(WIKI),
                   t("wiki_prune.reason_orphan", days=age), 0, age)


def spiky_done():
    """File names spiky_actions.py has mined. The state file lives on the
    worker and is not in git, so on another machine the check is skipped and
    the summary plus the age carry the decision: a report two weeks old has
    been through every 15 minute run there is."""
    if not SPIKY_DONE.exists():
        return None
    return set(read(SPIKY_DONE).splitlines())


def rule_spiky(now, summaries=None):
    if not SPIKY.exists():
        return
    summaries = summaries if summaries is not None else summary_index()
    done = spiky_done()
    for f in sorted(SPIKY.glob("*.md")):
        src = nfc(rel(f))
        if src not in summaries:
            continue
        if done is not None and f.name not in done:
            continue
        d = page_date(f, {})
        age = (now - d).days
        if age <= INBOX_STALE_DAYS:
            continue
        dst = SPIKY_ARCHIVE / d.strftime("%Y-%m") / f.name
        yield Move("spiky", f, dst, t("wiki_prune.reason_spiky", days=age), 0, age)


def candidates(now, only=None, files=None, graph=None):
    files = files if files is not None else all_wiki_files()
    graph = graph if graph is not None else link_graph(files)
    only = set(only or RULES)
    moves, taken = [], set()
    if "query" in only:
        for m in rule_query(graph, now):
            moves.append(m)
            taken.add(m.src)
    if "summary" in only:
        for m in rule_summary(graph, now):
            moves.append(m)
            taken.add(m.src)
    if "orphan" in only:
        for m in rule_orphan(graph, now, files, taken):
            moves.append(m)
            taken.add(m.src)
    if "spiky" in only:
        moves.extend(rule_spiky(now))
    return moves


# --------------------------------------------------------------------------
# Apply and log
# --------------------------------------------------------------------------

def ensure_archive_readme():
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    if not README_MD.exists():
        README_MD.write_text(no_dashes(t("wiki_prune.readme")) + "\n")


def retarget_summary(summaries, old_src, new_src):
    s = summaries.get(nfc(old_src))
    if not s:
        return
    text = read(s)
    new_text = re.sub(r"^source:\s*.*$", f"source: {new_src}", text, count=1, flags=re.M)
    if new_text != text:
        s.write_text(new_text)


def append_log(rows):
    """Both logs. The jsonl is machine state, outside git; LOG.md is the
    durable one and the one the owner reads."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(PRUNE_LOG, "a") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    header = [t("wiki_prune.log_title"), "", t("wiki_prune.log_intro"), "",
              t("wiki_prune.log_header"), "|---|---|---|---|---|"]
    new_lines = [f"| {r['date']} | {r['rule']} | `{r['from']}` | `{r['to']}` | {r['reason']} |"
                 for r in rows]
    if LOG_MD.exists():
        old = read(LOG_MD).splitlines()
        try:
            cut = next(i for i, ln in enumerate(old) if ln.startswith("|---")) + 1
            body = old[cut:]
        except StopIteration:
            body = []
    else:
        body = []
    LOG_MD.write_text(no_dashes("\n".join(header + new_lines + body)) + "\n")


def apply(moves, now):
    ensure_archive_readme()
    summaries = summary_index()
    rows, skipped = [], []
    for m in moves:
        if m.dst.exists():
            skipped.append(m)
            continue
        m.dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(m.src), str(m.dst))
        # NFC, as the compiler writes its source lines: macOS hands back NFD
        # names and the worker is Linux, where the two are different files.
        if m.rule == "spiky":
            retarget_summary(summaries, rel(m.src), nfc(rel(m.dst)))
        rows.append({"date": now.strftime("%Y-%m-%d"), "rule": m.rule,
                     "from": nfc(rel(m.src)), "to": nfc(rel(m.dst)), "reason": m.reason,
                     "inbound": m.inbound, "age_days": m.age})
    if rows:
        append_log(rows)
    return rows, skipped


# --------------------------------------------------------------------------
# Count
# --------------------------------------------------------------------------

def _section(text, heading):
    m = re.search(r"^## " + re.escape(heading) + r"\s*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    return m.group(1) if m else ""


def is_graded(decision_text):
    """/calibrate replaces `_Pending review._` under ## Outcome with the graded
    outcome. Anything else there, comments aside, counts as graded."""
    body = re.sub(r"<!--.*?-->", "", _section(decision_text, "Outcome"), flags=re.S).strip()
    return bool(body) and "pending review" not in body.lower()


def cluster_of(fm, p):
    """Which home a summary belongs to: the first two parts of its source
    (Work/Acme Co, Personal/Relocation). Digests point at a folder, not a
    file, and queries and articles carry no source; none of those has a home."""
    src = fm.get("source", "")
    if not src or src.endswith("/") or src.startswith("Thinking/Daily"):
        return None
    parts = [x for x in src.split("/") if x]
    if len(parts) >= 2:
        return "/".join(parts[:2])
    return None


def count_archived(now):
    cutoff = (now - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")
    n = 0
    for ln in read(LOG_MD).splitlines():
        m = re.match(r"^\|\s*(20\d\d-\d\d-\d\d)\s*\|", ln)
        if m and m.group(1) >= cutoff:
            n += 1
    return n


def count(now):
    cutoff = now - timedelta(days=WINDOW_DAYS)
    files = all_wiki_files()
    graph = link_graph(files)
    orphans = orphan_pages(files, graph)

    # Bridges, in Burt's sense: a concept page that draws its members from
    # two or more homes is a broker between clusters; one that only restates a
    # single home's notes is not, however many links it carries. The count is
    # made on concepts (articles before 2026-09-22) because that is where the
    # compiler joins sources.
    fms = {p: parse_fm(read(p)) for p in files}
    hubs = {WIKI / "concepts", WIKI / "articles"}
    homes_of = {}
    for a, b in graph["edges"]:
        if a.parent not in hubs:
            continue
        cb = cluster_of(fms.get(b, {}), b)
        if cb:
            homes_of.setdefault(a, set()).add(cb)
    cross = sum(1 for h in homes_of.values() if len(h) >= 2)
    same = sum(1 for h in homes_of.values() if len(h) == 1)

    queries = sorted(QUERIES.glob("*.md")) if QUERIES.exists() else []
    queries_30 = [q for q in queries if page_date(q) >= cutoff]

    decisions, graded, decisions_30 = [], [], []
    for d in sorted(DECISIONS.glob("*.md")) if DECISIONS.exists() else []:
        text = read(d)
        fm = parse_fm(text)
        if fm.get("type", "") != "decision":
            continue
        decisions.append(d)
        if is_graded(text):
            graded.append(d)
        if page_date(d, fm) >= cutoff:
            decisions_30.append(d)

    beliefs_30 = 0
    for b in sorted(BELIEFS.glob("*.md")) if BELIEFS.exists() else []:
        m = _DATE.search(parse_fm(read(b)).get("last_challenged", ""))
        if m and datetime.strptime(m.group(1), "%Y-%m-%d") >= cutoff:
            beliefs_30 += 1

    captures_30 = 0
    for base in CAPTURE_DIRS:
        if base.exists():
            captures_30 += sum(1 for p in base.rglob("*.md") if page_date(p, {}) >= cutoff)

    stale_cut = now - timedelta(days=INBOX_STALE_DAYS)
    inbox_stale = sum(1 for p in INBOX.rglob("*.md") if page_date(p, {}) < stale_cut) if INBOX.exists() else 0

    task_cut = (now - timedelta(days=TASK_STALE_DAYS)).strftime("%Y-%m-%d")
    tasks_open = tasks_stale = 0
    for ln in read(TASKS).splitlines():
        if not ln.startswith("- [ ]"):
            continue
        tasks_open += 1
        m = re.search(r"\|\s*(20\d\d-\d\d-\d\d)\s*$", ln)
        if m and m.group(1) <= task_cut:
            tasks_stale += 1

    lonely = 0
    seeds = sorted(IDEAS.glob("*.md")) if IDEAS.exists() else []
    if seeds:
        corpus = "\n".join(read(p) for p in files)
        corpus += "\n".join(read(p) for p in (VAULT / "Thinking").rglob("*.md")
                            if p.parent != IDEAS)
        for s in seeds:
            if (now - page_date(s)).days > SEED_LONELY_DAYS and f"[[{s.stem}" not in corpus:
                lonely += 1

    spiky_ready = sum(1 for _ in rule_spiky(now))

    # Graph health (orphan rate, degree, components, bridges): the direction
    # over weeks says whether ingestion still links, which daily use never shows.
    try:
        import wiki_metrics
        graph_health = wiki_metrics.compute(now)
    except Exception as e:
        print(f"graph health skipped: {e}", file=sys.stderr)
        graph_health = {}

    return {
        "date": now.strftime("%Y-%m-%d"),
        "captures_30": captures_30,
        "graded": len(graded),
        "decisions": len(decisions),
        "decisions_30": len(decisions_30),
        "queries": len(queries),
        "queries_30": len(queries_30),
        "inbox_stale": inbox_stale,
        "orphans": len(orphans),
        "bridge_cross": cross,
        "bridge_same": same,
        "beliefs_30": beliefs_30,
        "archived_30": count_archived(now),
        "tasks_open": tasks_open,
        "tasks_stale": tasks_stale,
        "seeds_lonely": lonely,
        "spiky_ready": spiky_ready,
        "wiki_pages": len(files),
        **{k: graph_health[k] for k in ("orphan_rate", "avg_degree", "main_share", "components",
                                        "stale_concepts", "bridges") if k in graph_health},
    }


def _ratio(a, b):
    return f"{a / b:.1f}" if b else f"{a} : 0"


def _bridge(c):
    total = c["bridge_cross"] + c["bridge_same"]
    return f"{c['bridge_cross'] / total:.2f}" if total else "n/a"


def previous_rows(md_text):
    """The run history is kept in the scorecard itself, so it travels with
    git and needs no state file on either machine."""
    rows = []
    for ln in md_text.splitlines():
        if re.match(r"^\|\s*20\d\d-\d\d-\d\d\s*\|", ln) and ln.count("|") >= 10:
            rows.append(ln.strip())
    return rows


def graph_rows(md_text):
    """Graph-health history rows: dated, eight columns (nine pipes), so they never
    mix with the run table above them."""
    return [ln.strip() for ln in md_text.splitlines()
            if re.match(r"^\|\s*20\d\d-\d\d-\d\d\s*\|", ln) and ln.count("|") == 9]


def graph_line(c):
    if "orphan_rate" not in c:
        return None
    bridges = ", ".join(f"[[{b}]]" for b in c.get("bridges", [])) or "-"
    return (f"| {c['date']} | {c['wiki_pages']} | {c['orphan_rate']:.0%} | {c['avg_degree']} | "
            f"{c['main_share']:.0%} | {c['components']} | {c['stale_concepts']} | {bridges} |")


def scorecard_markdown(c, moves=None):
    now_line = f"| {c['date']} | {_ratio(c['captures_30'], c['graded'])} | " \
               f"{_ratio(c['queries'], c['decisions'])} | {c['inbox_stale']} | {c['orphans']} | " \
               f"{_bridge(c)} | {c['decisions_30']} | {c['beliefs_30']} | {c['archived_30']} |"
    history = [r for r in previous_rows(read(SCORECARD_MD)) if not r.startswith(f"| {c['date']} ")]
    history = ([now_line] + history)[:SCORECARD_ROWS]

    lines = [t("wiki_prune.title"), "",
             t("wiki_prune.intro", ts=datetime.now().strftime("%Y-%m-%d %H:%M")), "",
             f"<!-- pile: {json.dumps(c, ensure_ascii=False)} -->", "",
             t("wiki_prune.now_heading"), "",
             f"| {t('wiki_prune.col_counter')} | {t('wiki_prune.col_value')} | {t('wiki_prune.col_note')} |",
             "|---|---|---|",
             f"| {t('wiki_prune.captures_per_graded', days=WINDOW_DAYS)} | {_ratio(c['captures_30'], c['graded'])} | "
             f"{t('wiki_prune.captures_per_graded_note', captures=c['captures_30'], graded=c['graded'])} |",
             f"| {t('wiki_prune.queries_per_decision')} | {_ratio(c['queries'], c['decisions'])} | "
             f"{t('wiki_prune.queries_per_decision_note', queries=c['queries'], decisions=c['decisions'])} |",
             f"| {t('wiki_prune.queries_30', days=WINDOW_DAYS)} | {c['queries_30']} | |",
             f"| {t('wiki_prune.inbox_stale', days=INBOX_STALE_DAYS)} | {c['inbox_stale']} | "
             f"{t('wiki_prune.inbox_stale_note', red=INBOX_STALE_RED)} |",
             f"| {t('wiki_prune.orphans')} | {c['orphans']} | {t('wiki_prune.orphans_note', pages=c['wiki_pages'])} |",
             f"| {t('wiki_prune.bridge')} | {_bridge(c)} | "
             f"{t('wiki_prune.bridge_note', cross=c['bridge_cross'], same=c['bridge_same'])} |",
             f"| {t('wiki_prune.decisions_30', days=WINDOW_DAYS)} | {c['decisions_30']} | |",
             f"| {t('wiki_prune.beliefs_30', days=WINDOW_DAYS)} | {c['beliefs_30']} | |",
             f"| {t('wiki_prune.archived_30', days=WINDOW_DAYS)} | {c['archived_30']} | |",
             "",
             t("wiki_prune.pending_heading"), "",
             t("wiki_prune.pending_spiky", n=c["spiky_ready"], days=INBOX_STALE_DAYS),
             t("wiki_prune.pending_tasks", n=c["tasks_stale"], days=TASK_STALE_DAYS, open=c["tasks_open"]),
             t("wiki_prune.pending_seeds", n=c["seeds_lonely"], days=SEED_LONELY_DAYS),
             ""]
    if moves is not None:
        lines += [t("wiki_prune.candidates_heading", n=len(moves)), ""]
        if moves:
            by_rule = Counter(m.rule for m in moves)
            lines += [f"- {rule}: {n}" for rule, n in sorted(by_rule.items())]
            lines.append("")
            for m in moves[:60]:
                lines.append(f"- `{rel(m.src)}` ({m.rule}, {m.reason})")
            if len(moves) > 60:
                lines.append(f"- ... +{len(moves) - 60}")
        else:
            lines.append(t("wiki_prune.candidates_none"))
        lines.append("")
    g_now = graph_line(c)
    g_hist = [r for r in graph_rows(read(SCORECARD_MD)) if not r.startswith(f"| {c['date']} ")]
    g_hist = (([g_now] if g_now else []) + g_hist)[:GRAPH_ROWS]
    if g_hist:
        lines += [t("wiki_prune.graph_heading", n=GRAPH_ROWS), "", t("wiki_prune.graph_intro"), "",
                  t("wiki_prune.graph_header"), "|---|---|---|---|---|---|---|---|"] + g_hist + [""]
    lines += [t("wiki_prune.runs_heading", n=SCORECARD_ROWS), "",
              t("wiki_prune.runs_header", days=INBOX_STALE_DAYS),
              "|---|---|---|---|---|---|---|---|---|"] + history
    return no_dashes("\n".join(lines)) + "\n"


def write_scorecard(c, moves=None):
    SCORECARD_MD.parent.mkdir(parents=True, exist_ok=True)
    SCORECARD_MD.write_text(scorecard_markdown(c, moves))


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", action="store_true", help="write the scorecard (default)")
    ap.add_argument("--archive", action="store_true", help="list what the rules would move")
    ap.add_argument("--apply", action="store_true", help="with --archive: move and log")
    ap.add_argument("--only", action="append", choices=RULES, help="run one rule (repeatable)")
    args = ap.parse_args()
    now = datetime.now()

    moves = None
    applied = 0
    if args.archive:
        moves = candidates(now, args.only)
        by_rule = Counter(m.rule for m in moves)
        log(t("wiki_prune.log_candidates", n=len(moves),
              detail=", ".join(f"{k} {v}" for k, v in sorted(by_rule.items())) or "none"))
        for m in moves:
            print(f"  {m.rule:<8} {rel(m.src)}  ->  {rel(m.dst)}   ({m.reason})")
        if args.apply and moves:
            rows, skipped = apply(moves, now)
            applied = len(rows)
            log(t("wiki_prune.log_applied", n=len(rows), skipped=len(skipped)))
            moves = None  # they are gone; the scorecard lists nothing stale

    if args.count or not args.archive or args.apply:
        c = count(now)
        write_scorecard(c, moves)
        log(t("wiki_prune.log_scorecard", path=rel(SCORECARD_MD), inbox=c["inbox_stale"],
              orphans=c["orphans"], qpd=_ratio(c["queries"], c["decisions"])))
    print(f"RUNLOG candidates={len(moves or [])} archived={applied}")


if __name__ == "__main__":
    main()
