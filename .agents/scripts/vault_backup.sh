#!/bin/bash
# Daily vault backup: commit everything and push to origin.
# Replaces the Obsidian Git plugin's "vault backup" commits, which silently
# stopped on 2026-07-21. Runs from launchd (<prefix>.brainless.backup).
cd "${BRAINLESS_VAULT:-$HOME/projects/brainless}" || exit 1

# 2026-09-03: for two days local commits were made but the push failed. The log
# showed "ssh: connect to host github.com port 22": launchd fires the job while
# the Mac is waking from sleep and Wi-Fi is not connected yet. The watchdog
# reported it as "the Mac has not committed for 63 hours". The network wait and
# the push retry below exist for that scenario.
github_reachable() {
  ssh -o BatchMode=yes -o ConnectTimeout=5 -T git@github.com 2>&1 \
    | grep -q "successfully authenticated"
}

wait_for_github() {
  local i
  for i in $(seq 1 12); do
    github_reachable && return 0
    sleep 5
  done
  return 1
}

# If a rebase was left half-done (a conflict in the previous run) the repo stays
# locked and every later pull/push fails. Clean up and notify; resolve by hand.
if [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; then
  git rebase --abort || true
  osascript -e 'display notification "Half-finished rebase cleaned up, manual sync needed" with title "brainless backup"' 2>/dev/null
  echo "$(date '+%Y-%m-%d %H:%M:%S') half-finished rebase aborted"
fi

if wait_for_github; then
  # Fetch the captures the worker pushed (since 2026-08-26 the Telegram
  # listener runs on the always-on Linux machine).
  git pull --rebase --autostash --quiet origin master || true
  if [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; then
    git rebase --abort || true
    osascript -e 'display notification "Pull conflict: manual sync needed" with title "brainless backup"' 2>/dev/null
    echo "$(date '+%Y-%m-%d %H:%M:%S') pull conflict, rebase aborted"
  fi
else
  echo "$(date '+%Y-%m-%d %H:%M:%S') github unreachable, pull skipped"
fi

# Guard: GitHub hard-rejects blobs over 100 MB and a single oversized file
# poisons every subsequent push. Auto-ignore anything over 95 MB before adding.
find . -type f -size +95M -not -path "./.git/*" -not -path "./_Backup/*" -not -path "./venv/*" -print0 | while IFS= read -r -d '' f; do
  rel="${f#./}"
  if ! git check-ignore -q "$rel"; then
    echo "$rel" >> .gitignore
    # Do NOT pass the file name into AppleScript (quote/backslash injection); fixed text.
    osascript -e 'display notification "95MB+ file added to gitignore (log: vault_backup.log)" with title "brainless backup"' 2>/dev/null
    echo "95MB+ gitignore: $rel"
  fi
done

if [ -n "$(git status --porcelain)" ]; then
  git add -A
  git commit -m "vault backup: $(date '+%Y-%m-%d %H:%M:%S')" || true
fi

# Push pause: while .agents/state/no_push exists, back up locally only.
# (Set 2026-08-04: first push after history rewrite is ~2 GB; the owner will
# trigger it manually when on a suitable connection, then delete the flag.)
if [ -f .agents/state/no_push ]; then
  exit 0
fi

# Push regardless of whether this run committed; earlier commits may be unpushed.
# 3 attempts: after each failed attempt sync first (possible non-fast-forward),
# then retry with an increasing wait.
push_ok=0
for attempt in 1 2 3; do
  if git push origin master --quiet; then
    push_ok=1
    break
  fi
  sleep $((attempt * 15))
  github_reachable || continue
  git pull --rebase --autostash --quiet origin master || true
  if [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; then
    git rebase --abort || true
    echo "$(date '+%Y-%m-%d %H:%M:%S') conflict during push retry, rebase aborted"
    break
  fi
done

if [ "$push_ok" -ne 1 ]; then
  osascript -e 'display notification "Vault backup push FAILED" with title "brainless"'
  exit 1
fi
