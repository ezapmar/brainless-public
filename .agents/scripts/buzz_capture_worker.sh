#!/bin/bash
# Buzz #inbox capture worker (worker, every 2 minutes via brainless-buzz-capture.timer).
# Mirrors telegram_worker.sh: pull, capture, commit only Thinking/ and Inbox/, push.
set -u
WORKER="$(grep -m1 '^worker_name:' "${BRAINLESS_VAULT:-$HOME/projects/brainless}/_Agent-Context/PROFILE.md" 2>/dev/null | cut -d: -f2- | xargs)"; WORKER="${WORKER:-worker}"
export PATH="$HOME/.cargo/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
export MISE_QUIET=1
VAULT="${BRAINLESS_VAULT:-$HOME/projects/brainless}"
cd "$VAULT" || exit 1

# Skip the tick if the box woke before the network came back, so the Buzz call
# does not crash the unit and trip a false alarm; the next run captures it.
if ! python3 tools/net_wait.py --wait; then
  echo "network not up yet (likely just woke); skipping this tick"
  exit 0
fi

git pull --rebase --autostash --quiet || true
python3 .agents/scripts/buzz_capture.py

git add "Thinking" "Inbox" 2>/dev/null
if ! git diff --cached --quiet; then
  git commit --quiet -m "buzz capture: $(date +%F-%H%M) ($WORKER)"
  git pull --rebase --autostash --quiet && git push --quiet
fi
