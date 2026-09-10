#!/bin/bash
# Worker generic job runner: nightly ailesi (closeout, nightly, lint,
# reconcile, dashboard, resurface) systemd timer'larindan bununla kosar.
# Once pull (gunun capture'lari gelsin), sonra verilen tool, sonra aninda
# commit+push (worker_backup) so the primary machine finds the result in the morning.
#
# The git lock wraps ONLY pull and backup here, never the tool run: a long job
# (dialectic ~18 min) must not hold the lock and starve the 15 min capture jobs.
# Units MUST NOT wrap this script in flock; that would re-serialise the whole run
# and, on the same lock file, self-block until the timeout.
# Usage: worker_job.sh tools/nightly_processor.py [args...]
set -u
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
export MISE_QUIET=1
VAULT="${BRAINLESS_VAULT:-$HOME/projects/brainless}"
LOCK="${BRAINLESS_GIT_LOCK:-$HOME/.brainless-git.lock}"
cd "$VAULT" || exit 1

flock -w 300 "$LOCK" git pull --rebase --autostash --quiet || true
python3 "$@"
status=$?
flock -w 300 "$LOCK" bash .agents/scripts/worker_backup.sh || true
exit "$status"
