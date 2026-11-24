#!/usr/bin/env python3
"""cloud_exclude_report.py: what P0.4 moved off the cloud, and a look at it.

The cloud_exclude folders are still compiled, on the local model (decision b of
P0.4). That makes two questions worth one command:

  1. Which .wiki pages came from those folders? Recompiling them changes what
     the owner and the search index see, so the list has to exist before the
     recompile, not after.
  2. Is the local model good enough for them? The 12B model is weak on long
     Turkish synthesis (the dialectic attempt produced 0 usable nights in 11),
     so five real sources are rendered first and read by a human. --sample
     writes nothing.

Read-only except --recompile, which rebuilds the summaries of these sources in
place on the local lane. It never touches the human tree and never deletes a
page: a failed call keeps the previous summary.

Usage:
  python3 tools/cloud_exclude_report.py                # the list
  python3 tools/cloud_exclude_report.py --sample 5     # local drafts, no writes
  python3 tools/cloud_exclude_report.py --recompile    # rebuild them locally
  python3 tools/cloud_exclude_report.py --recompile --budget-seconds 1800
"""
import argparse
import sys
import time
from pathlib import Path

import compile_resources as compiler
import cloud_gate

VAULT = compiler.VAULT
WIKI = compiler.WIKI


def home_sources(only=None):
    """(home, [source paths]) for every cloud_exclude home, source filter applied.

    The source filter (concepts.skip_reason) is the compiler's own rule about raw
    twins and reading sheets; recompiling a file the nightly would then archive
    would churn the wiki for nothing.
    """
    out = []
    for home in cloud_gate.CLOUD_EXCLUDE:
        if only and not home.startswith(only.strip("/")):
            continue
        root = VAULT / home
        files = [p for p in compiler.iter_sources([root]) if not compiler.source_skip(p)] \
            if root.exists() else []
        out.append((home, sorted(files)))
    return out


def summary_source(p: Path) -> str:
    from concepts import fm_value, split_frontmatter
    fm, _ = split_frontmatter(compiler.C.read_page(p))
    return fm_value(fm, "source") or ""


def derived_summaries():
    """.wiki/summaries pages whose source sits under a cloud_exclude home."""
    d = WIKI / "summaries"
    if not d.exists():
        return []
    return [p for p in sorted(d.glob("*.md")) if cloud_gate.cloud_excluded(summary_source(p))]


def derived_entities():
    """Entity dossiers that name a cloud-exclude home in their body.

    The dossier's frontmatter does not record its inputs, so this is a text
    match. It over-reports rather than under-reports on purpose: a page on the
    list that turns out to be safe costs a look, a page missing from it costs a
    leak.
    """
    d = WIKI / "entities"
    if not d.exists():
        return []
    needles = [home.split("/")[-1] for home in cloud_gate.CLOUD_EXCLUDE]
    out = []
    for p in sorted(d.glob("*.md")):
        text = compiler.C.read_page(p)
        if any(n and n in text for n in needles):
            out.append(p)
    return out


def derived_indexes(summaries):
    """_index pages that link one of the affected summaries."""
    d = WIKI / "_index"
    stems = {p.stem for p in summaries}
    if not d.exists() or not stems:
        return []
    return [p for p in sorted(d.glob("*.md"))
            if any(stem in compiler.C.read_page(p) for stem in stems)]


def report():
    homes = home_sources()
    summaries = derived_summaries()
    entities = derived_entities()
    indexes = derived_indexes(summaries)
    print("cloud_exclude (from _Agent-Context/PROFILE.md)")
    total = 0
    for home, files in homes:
        total += len(files)
        print(f"  {home}: {len(files)} source file(s)")
    print(f"  total: {total} source file(s)")
    print(f"\n.wiki summaries built from them: {len(summaries)}")
    for p in summaries:
        print(f"  {p.relative_to(VAULT)}  <- {summary_source(p)}")
    print(f"\n.wiki entity dossiers naming one of these areas: {len(entities)}")
    for p in entities:
        print(f"  {p.relative_to(VAULT)}")
    print(f"\n.wiki/_index pages linking an affected summary: {len(indexes)}")
    for p in indexes:
        print(f"  {p.relative_to(VAULT)}")
    print("\nNothing was changed. --sample N drafts N local summaries; --recompile "
          "rebuilds the list above on the local lane.")
    return summaries


def sample(n: int, only=None):
    """Draft n summaries on the local lane and print them. Writes nothing.

    Picks one per home in turn, then the next in each, so a sample of 3 covers
    every cloud_exclude folder instead of five chapters of the first one.
    """
    queues = [list(files) for _home, files in home_sources(only) if files]
    picked = []
    while len(picked) < n and any(queues):
        for queue in queues:
            if queue and len(picked) < n:
                picked.append(queue.pop(0))
    if not picked:
        print("no cloud-excluded source on disk", file=sys.stderr)
        return 1
    for src in picked:
        rel = src.relative_to(VAULT)
        body = src.read_text(encoding="utf-8")[:compiler.MAX_CHARS]
        prompt = compiler._summary_prompt(src, body)
        started = time.monotonic()
        out = compiler.call_claude(prompt, lane="compile-local", sources=[src])
        secs = time.monotonic() - started
        print(f"\n{'=' * 78}\n{rel}  ({len(body)} chars, {secs:.0f}s)")
        if not out:
            print("[FAIL] the local model produced nothing; the previous summary would stay")
            continue
        clean = compiler.clean_markdown_output(out)
        print(clean[:1800] + ("\n[...]" if len(clean) > 1800 else ""))
    print(f"\n{len(picked)} draft(s) shown. Nothing was written.")
    return 0


def recompile(budget_seconds: int, only=None):
    """Rebuild the affected summaries on the local lane, in place.

    full=True on purpose: the sources_hash of an existing summary still matches
    its source, so an incremental run would skip every one of them and the point
    of the exercise is to replace cloud-written text with local text.
    """
    deadline = time.monotonic() + budget_seconds
    done = failed = 0
    for _home, files in home_sources(only):
        for src in files:
            if time.monotonic() > deadline:
                print(f"[budget] {budget_seconds}s spent; the rest waits for the next run")
                return 0 if failed == 0 else 1
            if compiler.summarize_file(src, dry=False, full=True):
                done += 1
            else:
                failed += 1
    print(f"recompiled {done} summar{'y' if done == 1 else 'ies'} locally, {failed} failed "
          f"(a failed one keeps its previous page)")
    return 0 if failed == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sample", type=int, default=0, metavar="N",
                    help="draft N summaries on the local lane and print them (no writes)")
    ap.add_argument("--recompile", action="store_true",
                    help="rebuild the cloud-excluded summaries on the local lane")
    ap.add_argument("--budget-seconds", type=int, default=1800,
                    help="stop --recompile after this many seconds (default 1800)")
    ap.add_argument("--home", metavar="PREFIX", default=None,
                    help="limit --sample or --recompile to homes starting with this prefix")
    args = ap.parse_args()
    if args.sample:
        return sample(args.sample, args.home)
    if args.recompile:
        return recompile(args.budget_seconds, args.home)
    report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
