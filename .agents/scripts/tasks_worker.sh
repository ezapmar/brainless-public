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

git pull --rebase --autostash --quiet || true
python3 .agents/scripts/spiky_actions.py "$@"
[ -x "$GPY" ] && "$GPY" .agents/scripts/gtasks_sync.py
bash .agents/scripts/worker_backup.sh
