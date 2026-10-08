#!/bin/bash
# Shared pull for the worker wrappers: worker_backup.sh, telegram_worker.sh,
# buzz_capture_worker.sh, tasks_worker.sh and worker_job.sh.
#
# Until 2026-10-08 each wrapper ran `git pull --rebase --autostash || true`
# inline. When the rebase stopped on a conflict nothing aborted it, and the
# `git add -A && git commit` that followed committed conflict markers on a
# detached HEAD; every later run stayed stuck. This is the worker twin of the
# abort-and-alert logic in vault_backup.sh (the Mac side).
#
# What it does:
#   1. aborts a rebase left half-done by an earlier run
#   2. git pull --rebase --autostash (extra arguments go to git pull)
#   3. if that rebase stopped on a conflict: abort it, restore the autostash if
#      it landed in the stash list, log, and send one Buzz alert to #ops
#
# Exit status: 0 when the tree is safe to commit on (the pull worked, failed on
# the network, or a conflicting rebase was aborted and the local edits are
# back); 3 only when a rebase is still in progress or paths are unmerged (3, so
# a caller can tell it from a flock timeout, which exits 1). Callers must not
# commit on a non-zero exit. They skip the run and exit 0: the alert above goes
# out once per incident, while a failed unit would alert on every tick.
#
# It takes no lock of its own: run it inside the caller's existing flock.
# Usage: git_sync.sh [git pull arguments, e.g. origin master]
set -u
SCRIPTS="$(cd "$(dirname "$0")" && pwd)"
VAULT="${BRAINLESS_VAULT:-$(cd "$SCRIPTS/../.." && pwd)}"
cd "$VAULT" || exit 1

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') git_sync: $*" >&2; }

# One alert per incident: the stamp lives inside .git (never committed) and is
# cleared after the next clean sync. It is set only when the alert was queued,
# so a Buzz outage retries on the next run instead of going silent.
ALERT_STAMP="$(git rev-parse --git-path brainless-sync-alert)"
alert() {
  [ -e "$ALERT_STAMP" ] && return 0
  local host text
  host="$(hostname 2>/dev/null || echo worker)"
  text="git sync on $host: $1. Resolve by hand in $VAULT (git status)."
  # A hung Buzz call must not hold the caller's git lock for long.
  local guard=()
  command -v timeout >/dev/null 2>&1 && guard=(timeout 120)
  if printf '%s\n' "$text" | ${guard[@]+"${guard[@]}"} bash "$SCRIPTS/buzz_post.sh" watchdog ops >/dev/null 2>&1; then
    : > "$ALERT_STAMP"
  elif command -v osascript >/dev/null 2>&1; then
    # Fixed text only: never pass paths or git output into AppleScript.
    osascript -e 'display notification "Pull conflict: manual sync needed" with title "brainless git sync"' 2>/dev/null \
      && : > "$ALERT_STAMP"
  fi
  return 0
}

rebase_in_progress() {
  [ -d "$(git rev-parse --git-path rebase-merge)" ] || [ -d "$(git rev-parse --git-path rebase-apply)" ]
}

has_unmerged() {
  [ -n "$(git ls-files --unmerged 2>/dev/null)" ]
}

stash_count() {
  git stash list 2>/dev/null | wc -l | tr -d ' '
}

# Abort the rebase in progress. `git rebase --abort` re-applies the autostash
# itself; when that apply fails git stores it in the stash list instead, so a
# grown stash list whose top entry is an autostash is popped back here.
abort_rebase() {
  local before after
  before="$(stash_count)"
  git rebase --abort >/dev/null 2>&1 || log "git rebase --abort failed"
  after="$(stash_count)"
  if [ "$after" -gt "$before" ] && ! has_unmerged \
     && git stash list -1 2>/dev/null | grep -qi 'autostash'; then
    if git stash pop --quiet >/dev/null 2>&1; then
      log "autostash restored from the stash list"
    else
      log "autostash could not be restored; it stays in the stash list"
    fi
  fi
}

# 1. A rebase left over from an earlier run locks every later pull.
if rebase_in_progress; then
  log "half-finished rebase from an earlier run, aborting it"
  abort_rebase
  alert "a half-finished rebase from an earlier run was aborted"
fi

# 2. Pull. A network failure is not an error here: the caller commits locally
# and the next run pushes.
if git pull --rebase --autostash --quiet "$@"; then
  pulled=1
else
  pulled=0
fi

# 3. A conflicting rebase stops with the tree mid-rebase; undo it.
conflict=0
if rebase_in_progress; then
  conflict=1
  log "pull conflict, rebase aborted"
  abort_rebase
  alert "pull conflict, the rebase was aborted and local edits kept"
fi

if rebase_in_progress || has_unmerged; then
  log "tree is not clean (rebase in progress or unmerged paths); not safe to commit"
  alert "the tree is not clean after the pull (rebase in progress or unmerged paths), commits are paused"
  exit 3
fi

[ "$pulled" = 1 ] && rm -f "$ALERT_STAMP"
[ "$pulled" = 1 ] || [ "$conflict" = 1 ] || log "pull failed (network or remote); continuing with the local copy"
exit 0
