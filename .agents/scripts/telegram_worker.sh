#!/bin/bash
# 7/24 Linux worker (worker): Telegram capture + push.
# The Mac stays the primary author; this machine only writes to Thinking/Daily and pushes.
# A systemd timer calls it every 2 minutes (brainless-telegram.timer).
set -u
WORKER="$(grep -m1 '^worker_name:' "${BRAINLESS_VAULT:-$HOME/projects/brainless}/_Agent-Context/PROFILE.md" 2>/dev/null | cut -d: -f2- | xargs)"; WORKER="${WORKER:-worker}"
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
export MISE_QUIET=1  # mise's info lines mix into claude stdout and leak into the note
VAULT="${BRAINLESS_VAULT:-$HOME/projects/brainless}"
cd "$VAULT" || exit 1

git pull --rebase --quiet || true
python3 .agents/scripts/telegram_capture.py

git add "Thinking" "Inbox" ".wiki/digests/queries" 2>/dev/null  # the thinking loop writes under Thinking/
if ! git diff --cached --quiet; then
  git commit --quiet -m "telegram capture: $(date +%F-%H%M) ($WORKER)"
  git pull --rebase --quiet && git push --quiet
fi
