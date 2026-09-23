#!/bin/bash
# Worker vault backup: commit and push edits made on the always-on machine.
# Runs every 30 minutes (brainless-backup.timer). Lean twin of vault_backup.sh.
set -u
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
VAULT="${BRAINLESS_VAULT:-$HOME/projects/brainless}"
cd "$VAULT" || exit 1
# Commit suffix names the always-on worker (PROFILE.md worker_name); the watchdog attributes by it.
WORKER="$(grep -m1 '^worker_name:' _Agent-Context/PROFILE.md 2>/dev/null | cut -d: -f2- | xargs)"; WORKER="${WORKER:-worker}"

git pull --rebase --autostash --quiet || true

# Buzz Layer 1: post the morning briefing to #daily once per day (best effort).
[ -x .agents/scripts/buzz_briefing_sync.sh ] && bash .agents/scripts/buzz_briefing_sync.sh || true

# Batching: every timer job runs this, and run_log.py rewrites RUNS-<host>.md on
# each run, so a 2-minute poller alone made ~330 commits a day (23/09/2026).
# When the run log is the only change, commit it at most every QUIET_MIN
# minutes; any other change commits at once and carries the run log along.
QUIET_MIN="${BRAINLESS_BACKUP_QUIET_MIN:-30}"
changes="$(git status --porcelain)"
if [ -n "$changes" ]; then
  commit=1
  if ! printf '%s\n' "$changes" | cut -c4- | grep -qvE '^_Agent-Context/RUNS-[^/]+\.md$'; then
    last="$(git log -1 --format=%ct -- '_Agent-Context/RUNS-*.md' 2>/dev/null)"
    [ -n "$last" ] && [ $(( $(date +%s) - last )) -lt $(( QUIET_MIN * 60 )) ] && commit=0
  fi
  if [ "$commit" = 1 ]; then
    git add -A
    git commit --quiet -m "vault backup: $(date '+%Y-%m-%d %H:%M:%S') ($WORKER)" || true
  fi
fi

# Push only when there is something to push (a tool may also have committed).
if [ "$(git rev-list --count origin/master..HEAD 2>/dev/null || echo 1)" != 0 ]; then
  git push --quiet origin master || true
fi
