#!/bin/bash
# Worker generic job runner: the nightly family (closeout, nightly, lint,
# reconcile, dashboard, resurface) runs through this from its systemd timers.
# Pull first (so the day's captures arrive), then the given tool, then an immediate
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
export PYTHONPATH="$VAULT/tools:$VAULT/.agents/scripts${PYTHONPATH:+:$PYTHONPATH}"

# A timer can fire in the seconds after wake-from-sleep, before the network is
# back. Skip this tick cleanly rather than letting the tool crash on its first
# network call (LLM, Buzz, Google); the next timer run picks the work up.
if ! python3 tools/net_wait.py --wait; then
  echo "network not up yet (likely just woke); skipping this tick"
  exit 0
fi

# git_sync.sh aborts a conflicting rebase; exit 3 means the tree is still not
# clean, so skip the tool (some tools commit) rather than build on it. Any other
# failure (lock timeout, network) runs the tool on the local copy as before.
flock -w 300 "$LOCK" bash .agents/scripts/git_sync.sh
if [ $? -eq 3 ]; then
  echo "git_sync: tree not clean (alert sent once); skipping $*"
  exit 0
fi
# run_log records the run with its counts (tools/run_log.py) and returns the
# tool's own exit code, so OnFailure and the backup behave exactly as before.
python3 tools/run_log.py exec -- python3 "$@"
status=$?
flock -w 300 "$LOCK" bash .agents/scripts/worker_backup.sh || true
exit "$status"
