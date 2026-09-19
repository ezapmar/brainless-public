#!/bin/bash
# Task line (worker, every 30 min): Spiky action extraction + Google Tasks sync.
# Order matters: extraction first (new local items), then the two-way sync,
# then commit+push. Without the gtasks venv the sync step is silently skipped.
set -u
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
export MISE_QUIET=1
VAULT="${BRAINLESS_VAULT:-$HOME/projects/brainless}"
GPY="$HOME/.local/venvs/gtasks/bin/python"
cd "$VAULT" || exit 1

# Skip the tick if the box woke before the network came back, so the Google
# Tasks sync does not crash the unit and trip a false alarm; next run catches up.
if ! python3 tools/net_wait.py --wait; then
  echo "network not up yet (likely just woke); skipping this tick"
  exit 0
fi

git pull --rebase --autostash --quiet || true
python3 .agents/scripts/spiky_actions.py "$@"
[ -x "$GPY" ] && "$GPY" .agents/scripts/gtasks_sync.py
bash .agents/scripts/worker_backup.sh
