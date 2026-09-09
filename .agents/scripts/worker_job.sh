#!/bin/bash
# Worker generic job runner: nightly ailesi (closeout, nightly, lint,
# reconcile, dashboard, resurface) systemd timer'larindan bununla kosar.
# Once pull (gunun capture'lari gelsin), sonra verilen tool, sonra aninda
# commit+push (worker_backup) so the primary machine finds the result in the morning.
# Usage: worker_job.sh tools/nightly_processor.py [args...]
set -u
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
export MISE_QUIET=1
VAULT="${BRAINLESS_VAULT:-$HOME/projects/brainless}"
cd "$VAULT" || exit 1

git pull --rebase --autostash --quiet || true
python3 "$@"
status=$?
bash .agents/scripts/worker_backup.sh
exit "$status"
