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

# Skip the tick if the box woke before the network came back, so the Telegram
# fetch does not crash the unit and trip a false alarm; the next run captures it.
if ! python3 tools/net_wait.py --wait; then
  echo "network not up yet (likely just woke); skipping this tick"
  exit 0
fi

git pull --rebase --autostash --quiet || true
python3 .agents/scripts/telegram_capture.py

git add "Thinking" "Inbox" ".wiki/digests/queries" 2>/dev/null  # the thinking loop writes under Thinking/
if ! git diff --cached --quiet; then
  git commit --quiet -m "telegram capture: $(date +%F-%H%M) ($WORKER)"
  git pull --rebase --autostash --quiet && git push --quiet
fi
