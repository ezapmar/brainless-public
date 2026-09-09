#!/usr/bin/env python3
"""file_query.py — file a thinking-command's output into the wiki loopback.

This is the loopback that makes the wiki compound (see .wiki/_commands/_shared-rules.md):
command outputs are persisted under .wiki/digests/queries/ so future queries
(/context, /trace, /weekly, wiki_search) can see and build on them.

Usage:
  echo "<markdown output>" | python3 tools/file_query.py <command> "<title>"
  python3 tools/file_query.py decide "Housing in London" --summary "..." < out.md

Writes: .wiki/digests/queries/<YYYY-MM-DD>-<command>-<slug>.md  (with frontmatter)
Prints the relative path of the file written.
"""
import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# Portable: env override, else the repo that contains this script.
VAULT = Path(os.environ.get("BRAINLESS_VAULT") or Path(__file__).resolve().parents[1])
QDIR = VAULT / ".wiki" / "digests" / "queries"


def slugify(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE).strip().lower()
    return re.sub(r"[\s/]+", "-", s)[:60] or "untitled"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", help="command name, e.g. weekly, decide, ideas")
    ap.add_argument("title", help="short title for this run")
    ap.add_argument("--date", help="YYYY-MM-DD (default: today)")
    ap.add_argument("--summary", default="", help="one-line English summary")
    ap.add_argument("--lang", default="tr", choices=["tr", "en"], help="body language (default tr)")
    args = ap.parse_args()

    content = sys.stdin.read().strip()
    if not content:
        print("file_query: no content on stdin", file=sys.stderr)
        sys.exit(1)

    date = args.date or datetime.now().strftime("%Y-%m-%d")
    dst = QDIR / f"{date}-{args.command}-{slugify(args.title)}.md"
    QDIR.mkdir(parents=True, exist_ok=True)
    fm = (
        "---\n"
        f"lang: {args.lang}\n"
        f"summary_en: {args.summary or args.title}\n"
        f"command: {args.command}\n"
        f"title: {args.title}\n"
        f"compiled_at: {datetime.now().isoformat(timespec='seconds')}\n"
        "status: seed\n"
        f"tags: [query, {args.command}]\n"
        "---\n\n"
    )
    dst.write_text(fm + content + "\n")
    print(str(dst.relative_to(VAULT)))


if __name__ == "__main__":
    main()
