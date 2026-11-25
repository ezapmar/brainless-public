#!/bin/bash
# The night shift (launchd local.brainless.night, 23:00). One ordered
# chain instead of 20 timers: ingest, digest, compile, link, the weekly jobs,
# then the day's single commit and push. A step that fails is recorded and the
# chain goes on; night_last.json tells health_check how the night went.
# If the Mac was asleep at 23:00, launchd runs this on wake.
source "$(dirname "$0")/env.sh"
rotate

# One night at a time. launchd will not start a second copy of its own job, but
# nothing stops a hand-started run (a test, a catch-up) from overlapping the
# scheduled one, and a Sunday chain runs for hours. Two shifts on one checkout
# would both reach vault_backup.sh, the day's single commit and push.
LOCK="$VAULT/.agents/state/night.pid"
mkdir -p "$(dirname "$LOCK")"
if [ -f "$LOCK" ]; then
  held="$(cat "$LOCK" 2>/dev/null)"
  # The name check matters: a bare kill -0 would skip the night whenever the
  # kernel had reused a dead run's pid for anything at all.
  if [ -n "$held" ] && ps -o command= -p "$held" 2>/dev/null | grep -q night.sh; then
    echo "night: pid $held is still working, this run is skipped" >&2
    exit 0
  fi
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

FAILED=()
step() {  # step <label> <command...>
  run "$@" || FAILED+=("$1")
}

python3 tools/net_wait.py --wait || true   # the chain is mostly local; carry on

# The venv is the only Python with markitdown (requirements-core.txt), so the
# conversion step runs there; system python3 has neither markitdown nor numpy.
step ingest   "${BRAINLESS_SEARCH_PY:-python3}" .agents/scripts/smart_processor.py   # new files in Work/ Personal/ Library/
step nightly  python3 tools/nightly_processor.py           # the day's captures
# The close-out belongs to the evening it closes. A catch-up run the next
# morning would write it under the wrong date, so it is skipped then.
if [ "$(date +%H)" -ge 20 ]; then
  step closeout python3 tools/evening_closeout.py
fi
# In the venv as well: the compile's last stages (contradiction check, retrieval
# score) need the search libraries. Under system python3 they skipped without a
# word from 15/11 to 19/11/2026: no contradiction was checked on any night and
# the nightly score was BM25 only.
step compile  "${BRAINLESS_SEARCH_PY:-python3}" tools/nightly_compile.py   # wiki pages, concepts, index
step index    "${BRAINLESS_SEARCH_PY:-python3}" tools/semantic_index.py build   # hybrid search vectors
step links    "${BRAINLESS_SEARCH_PY:-python3}" tools/link_suggest.py
# After the graph has settled: 2 s, no model. health_check reads the report, so
# the counts reach the morning briefing instead of waiting for Sunday, which is
# how 474 broken links and 108 orphans accumulated unseen. Links to a page that
# exists under another name are repointed, and a concept page loses the links
# that only it asks for (the compiler links every first mention; see
# lint_wiki.lonely_targets). Shared missing targets stay as entity candidates.
step lint     python3 tools/lint_wiki.py --fix-links --prune-lonely concepts
step vault-graph python3 tools/vault_graph.py
step attention python3 tools/attention_audit.py

if [ "$(date +%u)" = 7 ]; then                             # Sunday
  # One numbered message a week, answered with one reply. dreaming imports
  # link_suggest, so it needs the venv too: numpy lives only there.
  step dreaming  "${BRAINLESS_SEARCH_PY:-python3}" tools/dreaming.py   # link proposals to Telegram
  step prune     python3 tools/wiki_prune.py --count --archive --apply
  step research  python3 tools/weekly_research.py
  step radar     python3 .agents/scripts/relationship_radar.py
  step thinking  python3 .agents/scripts/thinking_loop.py --ask
  step reconcile python3 tools/weekly_reconcile.py
  step kill-review python3 tools/kill_review.py              # overdue kill criteria, one message
  step lint-fix  python3 tools/lint_wiki.py --fix --fix-links   # rewrites the report, post-fix
  # Weekly, not monthly: since 15/11/2026 this Mac is the only working copy, and
  # an archive on the 1st left up to a month of private-home edits (they are not
  # in git) with no copy that sync cannot reach. A missed Sunday is caught up the
  # next one; health_check goes yellow at 9 days and red at 21.
  step archive   python3 tools/vault_archive.py create --if-older-days 6
fi
case "$(date +%m-%d)" in
  0[13579]-01|11-01) step writing-ideas python3 tools/writing_ideas.py ;;
esac

python3 tools/health_check.py >> logs/health_check.log 2>&1
step backup bash .agents/scripts/vault_backup.sh             # the day's one commit + push

python3 - "${FAILED[@]}" <<'EOF'
import json, sys, time
path = ".agents/state/night_last.json"
json.dump({"finished_at": int(time.time()), "failed": sys.argv[1:]}, open(path, "w"))
EOF
[ "${#FAILED[@]}" -eq 0 ]
