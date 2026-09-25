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

    # The change brief: what the compile added, updated, linked and flagged,
    # read by the morning briefing (_Agent-Context/WIKI-CHANGES.md).
    changed = ""
    if before is not None:
        try:
            d = wiki_changes.write(before, wiki_changes.snapshot())
            changed = f" wiki_changed={wiki_changes.count(d)}"
        except Exception as e:
            print(f"change brief skipped: {e}")

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
    print(f"RUNLOG compile_rc={compile_rc}{changed}{hit5}"
          + ("" if compile_rc == 0 else " status=partial"))


if __name__ == "__main__":
    main()
