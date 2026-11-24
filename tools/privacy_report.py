#!/usr/bin/env python3
"""privacy_report.py: what left the machine this week, lane by lane.

The egress ledger (tools/llm.py) records counts, never text. This reads the last
seven days of it and answers the question the owner actually asks: how much of
my vault went to a cloud provider, which lane sent it, and how much did the
redaction gate have to mask on the way out.

Two numbers matter, and they are different:

  cloud characters   the volume that left the machine. Input characters are the
                     vault text itself; output characters are what the model
                     wrote back, which is new text and not a leak of the vault.
                     Both are reported, separately, per lane.
  local ratio        share of calls that never left the machine. A call is
                     counted once per send row, so a refusal (`sent: false`)
                     does not inflate it: nothing left.

The report is written to logs/privacy/ (gitignored), one markdown file per run
plus the raw JSON, so a week can be compared with the next one without a
database. Nothing here writes to the vault or to the ledger.

Usage:
  python3 tools/privacy_report.py                # last 7 days, write the report
  python3 tools/privacy_report.py --days 30
  python3 tools/privacy_report.py --print        # also print it
  python3 tools/privacy_report.py --summary      # one line for a briefing
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta

from paths import vault_root
import config  # noqa: F401  brainless.toml sets env defaults before anything reads env
import llm

VAULT = vault_root()
OUT_DIR = os.path.join(VAULT, "logs", "privacy")
STAMP = "%Y-%m-%d %H:%M:%S"


def read_rows(path: str | None = None, now: datetime | None = None,
              days: int = 7) -> list[dict]:
    """Ledger rows inside the window, oldest first. Unparsable lines are skipped."""
    since = (now or datetime.now()) - timedelta(days=days)
    rows = []
    try:
        with open(path or llm.EGRESS_FILE, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                try:
                    if datetime.strptime(str(row.get("ts", "")), STAMP) < since:
                        continue
                except ValueError:
                    continue
                rows.append(row)
    except OSError:
        pass
    return rows


def build(rows: list[dict], days: int = 7, now: datetime | None = None) -> dict:
    """Aggregate ledger rows into the report shape.

    A send row (`phase: start`) is one attempted call. Its output length lives
    on the matching completion row (`phase: end`, same id), which is why the two
    are joined here rather than at write time.
    """
    end_by_id = {str(r.get("id")): r for r in rows if r.get("phase") == "end"}
    lanes: dict[str, dict] = {}
    totals = {"calls": 0, "cloud_calls": 0, "local_calls": 0, "refused": 0,
              "in_chars": 0, "cloud_in_chars": 0, "out_chars": 0, "cloud_out_chars": 0,
              "web_calls": 0, "redactions": {}}
    for row in rows:
        if str(row.get("phase") or "start") != "start":
            continue
        lane = str(row.get("lane") or "-")
        bucket = lanes.setdefault(lane, {"calls": 0, "cloud_calls": 0, "local_calls": 0,
                                         "refused": 0, "in_chars": 0, "out_chars": 0,
                                         "cloud_in_chars": 0, "cloud_out_chars": 0,
                                         "redactions": {}})
        if not row.get("sent", True):
            bucket["refused"] += 1
            totals["refused"] += 1
            continue
        # Rows written before 2026-11-21 have no `local` key; derive it from the
        # provider rather than counting an old local call as a cloud one.
        local = row.get("local")
        local = llm._is_local(str(row.get("provider", ""))) if local is None else bool(local)
        chars_in = int(row.get("in") or 0)
        # The output length is on the completion row for a 2026-11-21 row and
        # inline for an older one; both shapes are read so the ledger needs no
        # migration.
        end = end_by_id.get(str(row.get("id"))) or {}
        chars_out = int(end["out"] if "out" in end else (row.get("out") or 0))
        bucket["calls"] += 1
        bucket["in_chars"] += chars_in
        bucket["out_chars"] += chars_out
        bucket["local_calls" if local else "cloud_calls"] += 1
        totals["calls"] += 1
        totals["in_chars"] += chars_in
        totals["out_chars"] += chars_out
        totals["local_calls" if local else "cloud_calls"] += 1
        if not local:
            bucket["cloud_in_chars"] += chars_in
            bucket["cloud_out_chars"] += chars_out
            totals["cloud_in_chars"] += chars_in
            totals["cloud_out_chars"] += chars_out
        if row.get("web"):
            totals["web_calls"] += 1
        for kind, count in (row.get("redactions") or {}).items():
            bucket["redactions"][kind] = bucket["redactions"].get(kind, 0) + int(count)
            totals["redactions"][kind] = totals["redactions"].get(kind, 0) + int(count)
    for bucket in lanes.values():
        bucket["local_ratio"] = (bucket["local_calls"] / bucket["calls"]) if bucket["calls"] else 0.0
    totals["local_ratio"] = (totals["local_calls"] / totals["calls"]) if totals["calls"] else 0.0
    return {
        "generated_at": (now or datetime.now()).strftime(STAMP),
        "days": days,
        "totals": totals,
        "lanes": dict(sorted(lanes.items(), key=lambda kv: -kv[1]["in_chars"])),
    }


def _redactions(counts: dict) -> str:
    if not counts:
        return "-"
    return ", ".join(f"{kind} {count}" for kind, count in sorted(counts.items()))


def render(report: dict) -> str:
    """The report as markdown: totals first, then one row per lane."""
    t = report["totals"]
    lines = [
        "# Privacy report: egress ledger",
        "",
        f"Window: last {report['days']} day(s), generated {report['generated_at']}.",
        "Counts only: the ledger holds no prompt text and no source path.",
        "",
        "## Totals",
        "",
        "| Measure | Value |",
        "|---|---|",
        f"| Calls | {t['calls']} |",
        f"| Local calls | {t['local_calls']} ({t['local_ratio']:.0%}) |",
        f"| Cloud calls | {t['cloud_calls']} |",
        f"| Refused by the gate (nothing sent) | {t['refused']} |",
        f"| Characters in, cloud | {t['cloud_in_chars']} |",
        f"| Characters out, cloud | {t['cloud_out_chars']} |",
        f"| Characters in, all lanes | {t['in_chars']} |",
        f"| Web calls | {t['web_calls']} |",
        f"| Redactions | {_redactions(t['redactions'])} |",
        "",
        "## By lane",
        "",
        "| Lane | Calls | Local | Local ratio | Cloud in | Cloud out | Redactions |",
        "|---|---|---|---|---|---|---|",
    ]
    for lane, b in report["lanes"].items():
        lines.append(f"| `{lane}` | {b['calls']} | {b['local_calls']} | "
                     f"{b['local_ratio']:.0%} | {b['cloud_in_chars']} | "
                     f"{b['cloud_out_chars']} | {_redactions(b['redactions'])} |")
    if not report["lanes"]:
        lines.append("| (no calls in the window) | 0 | 0 | - | 0 | 0 | - |")
    lines.append("")
    return "\n".join(lines)


def summary_line(report: dict) -> str:
    """One line for the briefing: cloud characters, local ratio, redactions."""
    t = report["totals"]
    return (f"{report['days']}d: {t['cloud_in_chars']} chars to the cloud, "
            f"local {t['local_ratio']:.0%} of {t['calls']} calls, "
            f"redactions {_redactions(t['redactions'])}")


def write_report(report: dict, out_dir: str | None = None) -> tuple[str, str]:
    """Write the markdown and the JSON under logs/privacy/. Returns both paths."""
    out_dir = out_dir or OUT_DIR
    os.makedirs(out_dir, exist_ok=True)
    stamp = report["generated_at"][:10]
    md = os.path.join(out_dir, f"privacy-report-{stamp}.md")
    js = os.path.join(out_dir, f"privacy-report-{stamp}.json")
    with open(md, "w", encoding="utf-8") as fh:
        fh.write(render(report))
    with open(js, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return md, js


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=7, help="window in days (default 7)")
    ap.add_argument("--print", dest="show", action="store_true", help="print the report too")
    ap.add_argument("--summary", action="store_true", help="print one line only, write nothing")
    args = ap.parse_args()
    report = build(read_rows(days=args.days), days=args.days)
    if args.summary:
        print(summary_line(report))
        return 0
    md, js = write_report(report)
    print(md)
    print(js)
    if args.show:
        print()
        print(render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
