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

# Buzz Katman 1: post the morning briefing to #gunluk once per day (best effort).
[ -x .agents/scripts/buzz_briefing_sync.sh ] && bash .agents/scripts/buzz_briefing_sync.sh || true

if [ -n "$(git status --porcelain)" ]; then
  git add -A
  git commit --quiet -m "vault backup: $(date '+%Y-%m-%d %H:%M:%S') ($WORKER)" || true
fi

git push --quiet origin master || true
