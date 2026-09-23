#!/bin/bash
# Enhanced PATH so user tools (claude, node) are discoverable.
# markitdown is used as a Python library (see tools/markitdown_native.py), not
# the CLI, so it no longer needs to be on PATH: only importable. Install it via:
#   python3 -m pip install --break-system-packages --user 'markitdown[pdf,docx,xlsx,pptx]==0.1.7'
# NOTE: do NOT use markitdown[all] on Python 3.14: its youtube-transcript-api
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

python3 tools/run_log.py exec -- python3 .agents/scripts/smart_processor.py >> "$LOG" 2>&1
status=$?

# Failure alerting: the processor exits non-zero when conversions fail. Surface
# it instead of letting it rot silently (the processor also fires its own
# notification with detail; this is the belt-and-suspenders fallback).
if [ "$status" -ne 0 ]; then
  osascript -e 'display notification "smart_processor exited with errors, check logs/smart_processor.log" with title "brainless"' 2>/dev/null
fi

# Hourly CRM snapshot (read-only, no LLM). Silent no-op without a token in
# ~/.config/brainless/. Runs before health_check so HEALTH.md sees fresh status.
CRM_LOG="logs/crm_capture.log"
if [ -f "$CRM_LOG" ] && [ "$(stat -f%z "$CRM_LOG" 2>/dev/null || echo 0)" -gt "$MAX_BYTES" ]; then
  mv -f "$CRM_LOG" "$CRM_LOG.1"
fi
python3 .agents/scripts/crm_capture.py >> "$CRM_LOG" 2>&1
bash .agents/scripts/buzz_crm_sync.sh >> "$CRM_LOG" 2>&1

# Monthly encrypted archive to the Drive folder (no-op until 30 days have
# passed; tools/vault_archive.py). Runs on the Mac, where Drive is mounted.
python3 tools/run_log.py exec -- python3 tools/vault_archive.py create --if-older-days 30 >> logs/backup.log 2>&1

# Hourly heartbeat: refresh _Agent-Context/HEALTH.md (cheap, no LLM calls).
python3 tools/health_check.py >> logs/health_check.log 2>&1

# Commit the regenerated context blocks right here so the working tree does not
# sit dirty for up to a day between the 21:30 vault_backup. These three files
# are deterministic status blocks (no LLM), rewritten every run; the always-on
# machine already commits its generated output at generation time via
# worker_job.sh, and this applies the same discipline to the laptop.
# Local commit only, scoped to the generated files by pathspec so human-authored
# vault edits are never swept in. vault_backup.sh keeps ownership of the push
# (its network-wait and rebase-recovery logic stays the single place for that).
GEN_FILES=()
for f in _Agent-Context/CRM.md _Agent-Context/HEALTH.md _Agent-Context/KILL-CRITERIA.md _Agent-Context/RUNS-mac.md; do
  [ -f "$f" ] && GEN_FILES+=("$f")
done
# The run log is created on its first run; a pathspec commit needs it tracked.
[ -f _Agent-Context/RUNS-mac.md ] && git add -- _Agent-Context/RUNS-mac.md 2>/dev/null
if [ "${#GEN_FILES[@]}" -gt 0 ] && ! git diff --cached --quiet -- "${GEN_FILES[@]}" 2>/dev/null; then
  git commit --quiet -m "context refresh: $(date '+%F-%H%M')" -- "${GEN_FILES[@]}" 2>/dev/null || true
fi

exit "$status"
