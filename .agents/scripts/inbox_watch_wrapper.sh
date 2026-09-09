#!/bin/bash
# Fires on every change to Inbox/ (launchd WatchPaths, <prefix>.brainless.inbox)
# and OCRs any image dropped there into a Thinking/Daily capture. Mirrors the PATH
# setup of cron_wrapper.sh so the claude CLI (and its node runtime) are found.
PY_BINS=$(ls -d "$HOME"/Library/Python/*/bin 2>/dev/null | sort -Vr | tr '\n' ':')
NODE_BIN=$(ls -d "$HOME"/.nvm/versions/node/*/bin 2>/dev/null | sort -V | tail -1)
export PATH="${PY_BINS}$HOME/.local/bin:${NODE_BIN:+$NODE_BIN:}/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
cd "${BRAINLESS_VAULT:-$HOME/projects/brainless}" || exit 1

LOG="logs/inbox_watch.log"
MAX_BYTES=$((5 * 1024 * 1024))   # rotate at 5 MB, keep one previous generation
if [ -f "$LOG" ]; then
  size=$(stat -f%z "$LOG" 2>/dev/null || echo 0)
  if [ "$size" -gt "$MAX_BYTES" ]; then
    mv -f "$LOG" "$LOG.1"
  fi
fi

python3 .agents/scripts/smart_processor.py --images >> "$LOG" 2>&1
