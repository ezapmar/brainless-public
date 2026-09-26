#!/usr/bin/env python3
"""Nightly wiki compile (worker, brainless-compile.timer at 23:20).

Compile, change brief, lint report, semantic index + link suggestions,
retrieval check. Until 23/09/2026 this block sat inside nightly_processor.py
after the digest, so it ran only on nights with captures and a good summary: a
quiet day (17 and 22/09) left edits in Work/, Personal/ and Library/
uncompiled, and the index and the retrieval number went stale with them. Now it runs every night on its own.

Run: python3 tools/nightly_compile.py   (via worker_job.sh: pull, run, backup)
"""
import os
import subprocess
import sys
import time
from datetime import datetime

VAULT_ROOT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# The compile makes serial LLM calls of up to 300s each. 30 minutes cut a large
# backlog short every night (Sep 8-10, 2026); 90 minutes lets it drain.
COMPILE_TIMEOUT = int(os.environ.get("BRAINLESS_COMPILE_TIMEOUT", "5400"))
# A window alone was not enough: a backlog larger than the window meant the hard
# kill landed mid-phase every night (Sep 15, 17, 19, 21, 2026), took the cheap
# phases behind it (INDEX.md) with it, and threw away the child's buffered
# output, so the journal could not even show where it stopped. The compile now
# gets its own smaller budget and stops itself between items; the timeout stays
# as the backstop for a genuine hang, and the child runs unbuffered so its
# progress reaches the journal as it happens.
COMPILE_BUDGET = int(os.environ.get("BRAINLESS_COMPILE_BUDGET",
                                    str(max(600, COMPILE_TIMEOUT - 600))))
# The nightly concept-assign only proposes from the summaries it is handed that
# night, so two related sources compiled weeks apart never meet. Once a week
# (Sunday by default) concept-propose reads the whole catalogue. About four
# model calls; it adds nothing while the review queue is full.
PROPOSE_WEEKDAY = int(os.environ.get("BRAINLESS_CONCEPT_PROPOSE_WEEKDAY", "6"))  # Monday = 0
PROPOSE_TIMEOUT = 1500
# The unit's hard limit is TimeoutStartSec=2h. Optional steps start only while
# this much of it is left unused, so a long compile never gets the job killed.
HARD_LIMIT = 7200
STARTED = time.monotonic()


def time_left() -> float:
    return HARD_LIMIT - (time.monotonic() - STARTED)


def weekly_propose() -> str:
    """Run concept-propose on its weekday; the RUNLOG fragment it earned."""
    if datetime.now().weekday() != PROPOSE_WEEKDAY:
        return ""
    if time_left() < PROPOSE_TIMEOUT + 1200:
        print("concept-propose skipped: the compile used the time")
        return " concept_propose=late"
    try:
        import concept_review
        before = {r["slug"] for r in concept_review.pending()}
        if not concept_review.room():
            print(f"concept-propose skipped: {len(before)} proposals wait for a decision")
            return " concept_propose=skipped"
        subprocess.run([sys.executable, "-u", os.path.join(VAULT_ROOT, "tools/compile_resources.py"),
                        "--only", "concept-propose", f"--budget-seconds={PROPOSE_TIMEOUT - 300}"],
                       cwd=VAULT_ROOT, check=False, timeout=PROPOSE_TIMEOUT)
        new = {r["slug"] for r in concept_review.pending()} - before
        return f" concept_proposed={len(new)}"
    except Exception as e:
        print(f"concept-propose error: {e}")
        return " concept_propose=fail"


def main():
    # Its exit code used to be dropped (check=False), so a night where most
    # model calls failed still looked like a clean run.
    compile_rc = -1
    # Snapshot .wiki/ first so the change brief can say what this compile did.
    before = None
    try:
        import wiki_changes
        before = wiki_changes.snapshot()
    except Exception as e:
        print(f"change brief snapshot skipped: {e}")
    try:
        compile_rc = subprocess.run(
            [sys.executable, "-u",
             os.path.join(VAULT_ROOT, 'tools/compile_resources.py'),
             f"--budget-seconds={COMPILE_BUDGET}"],
            cwd=VAULT_ROOT, check=False, timeout=COMPILE_TIMEOUT,
        ).returncode
    except Exception as e:
        print(f"compile_resources error: {e}")

    proposed = weekly_propose()
    # Any proposal not yet in Buzz (a failed send, a hand-added row) goes out now.
    try:
        import concept_review
        concept_review.announce()
    except Exception as e:
        print(f"concept proposal announcement skipped: {e}")

    after = None
    if before is not None:
        try:
            after = wiki_changes.snapshot()
        except Exception as e:
            print(f"change brief snapshot skipped: {e}")

    # Always refresh the lint report (non-blocking, report-only mode)
    try:
        subprocess.run(
            [sys.executable, os.path.join(VAULT_ROOT, 'tools/lint_wiki.py')],
            cwd=VAULT_ROOT, check=False, timeout=180,
        )
    except Exception as e:
        print(f"lint_wiki report refresh skipped: {e}")
    # Semantic index follows the compile: only pages whose text changed
    # are embedded again. A no-op where the search addon is not installed.
    try:
        import semantic_index
        if semantic_index.available():
            r = semantic_index.Index().build()
            print(f"semantic index: {r['embedded']} passages embedded, {r['pages']} pages")
            # Link suggestions read the fresh index. Report only: nothing is applied.
            import link_suggest
            s = link_suggest.suggest()
            if s:
                link_suggest.REPORT.write_text(link_suggest.markdown(s))
                print(f"link suggestions: {len(s['orphan_homes'])} orphan homes, "
                      f"{len(s['new_links'])} new links, {len(s['near_identical'])} near-identical")
    except Exception as e:
        print(f"semantic index skipped: {e}")

    # Contradictions: tonight's summaries against their nearest pages, on the
    # index just rebuilt. Needs the search addon; stops at a time budget.
    flags, conflicts = [], ""
    if after is not None and time_left() > 1500:
        try:
            import semantic_index
            if semantic_index.available():
                import contradiction_check as cc
                pages = wiki_changes.changed_pages(before, after, "summaries")
                added, n = cc.run_night(pages, deadline=time.monotonic() + time_left() - 900)
                flags = cc.brief_lines(added)
                conflicts = f" checked={n['checked']} conflicts={n['conflicts']}"
        except Exception as e:
            print(f"contradiction check skipped: {e}")

    # The change brief: what the compile added, updated, linked and flagged,
    # read by the morning briefing (_Agent-Context/WIKI-CHANGES.md).
    changed = ""
    if after is not None:
        try:
            d = wiki_changes.write(before, after, conflicts=flags)
            changed = f" wiki_changed={wiki_changes.count(d)}"
        except Exception as e:
            print(f"change brief skipped: {e}")
    # Retrieval check after the compile: the same golden questions every
    # night, so a compile change that hurts search shows up as a number.
    hit5 = ""
    try:
        import retrieval_eval
        res = retrieval_eval.evaluate()
        if res["n"]:
            hit5 = f" hit5={res['hit5']}"
    except Exception as e:
        print(f"retrieval eval skipped: {e}")
    print(f"RUNLOG compile_rc={compile_rc}{changed}{proposed}{conflicts}{hit5}"
          + ("" if compile_rc == 0 else " status=partial"))


if __name__ == "__main__":
    main()
