#!/bin/bash
# Enhanced PATH so markitdown (and other user tools) are discoverable.
# markitdown (with document extras) is installed via:
#   python3 -m pip install --break-system-packages --user 'markitdown[pdf,docx,xlsx,pptx]==0.1.7'
# NOTE: do NOT use markitdown[all] on Python 3.14 — its youtube-transcript-api
# pin is unsatisfiable there and pip will silently downgrade markitdown to 0.0.2.
# Resolve user-script and node bin dirs dynamically so a Python/node version
# bump doesn't silently break the cron job (no pinned v22.19.0 / 3.14 paths).
PY_BINS=$(ls -d "$HOME"/Library/Python/*/bin 2>/dev/null | sort -Vr | tr '\n' ':')
NODE_BIN=$(ls -d "$HOME"/.nvm/versions/node/*/bin 2>/dev/null | sort -V | tail -1)
export PATH="${PY_BINS}$HOME/.local/bin:${NODE_BIN:+$NODE_BIN:}/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
cd "${BRAINLESS_VAULT:-$HOME/projects/brainless}" || exit 1
# Prefer the vault's own virtualenv when the installer created one.
if [ -x .venv/bin/python3 ]; then export PATH="$PWD/.venv/bin:$PATH"; fi
mkdir -p logs

LOG="logs/smart_processor.log"
MAX_BYTES=$((5 * 1024 * 1024))   # rotate at 5 MB, keep one previous generation

# Log rotation: keep the log from growing unbounded (it once hit 14 MB).
if [ -f "$LOG" ]; then
  size=$(stat -f%z "$LOG" 2>/dev/null || echo 0)
  if [ "$size" -gt "$MAX_BYTES" ]; then
    mv -f "$LOG" "$LOG.1"
  fi
fi

python3 .agents/scripts/smart_processor.py >> "$LOG" 2>&1
status=$?

# Failure alerting: the processor exits non-zero when conversions fail. Surface
# it instead of letting it rot silently (the processor also fires its own
# notification with detail; this is the belt-and-suspenders fallback).
if [ "$status" -ne 0 ]; then
  osascript -e 'display notification "smart_processor exited with errors — check logs/smart_processor.log" with title "brainless"' 2>/dev/null
fi

# Hourly CRM snapshot (read-only, no LLM). Silent no-op without a token in
# ~/.config/brainless/. Runs before health_check so HEALTH.md sees fresh status.
CRM_LOG="logs/crm_capture.log"
if [ -f "$CRM_LOG" ] && [ "$(stat -f%z "$CRM_LOG" 2>/dev/null || echo 0)" -gt "$MAX_BYTES" ]; then
  mv -f "$CRM_LOG" "$CRM_LOG.1"
fi
python3 .agents/scripts/crm_capture.py >> "$CRM_LOG" 2>&1
bash .agents/scripts/buzz_crm_sync.sh >> "$CRM_LOG" 2>&1

# Hourly heartbeat: refresh _Agent-Context/HEALTH.md (cheap, no LLM calls).
python3 tools/health_check.py >> logs/health_check.log 2>&1

exit "$status"
