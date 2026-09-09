#!/bin/bash
# Buzz #inbox capture worker (worker, every 2 minutes via brainless-buzz-capture.timer).
# Mirrors telegram_worker.sh: pull, capture, commit only Thinking/ and Inbox/, push.
set -u
WORKER="$(grep -m1 '^worker_name:' "${BRAINLESS_VAULT:-$HOME/projects/brainless}/_Agent-Context/PROFILE.md" 2>/dev/null | cut -d: -f2- | xargs)"; WORKER="${WORKER:-worker}"
export PATH="$HOME/.cargo/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
export MISE_QUIET=1
VAULT="${BRAINLESS_VAULT:-$HOME/projects/brainless}"
cd "$VAULT" || exit 1

git pull --rebase --quiet || true
python3 .agents/scripts/buzz_capture.py

git add "Thinking" "Inbox" 2>/dev/null
if ! git diff --cached --quiet; then
  git commit --quiet -m "buzz capture: $(date +%F-%H%M) ($WORKER)"
  git pull --rebase --quiet && git push --quiet
fi
