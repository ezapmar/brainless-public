#!/bin/bash
# setup.sh: stand up the brainless automation on a fresh machine.
# Idempotent: safe to re-run. Installs Python deps and checks external tools.
PREFIX="${BRAINLESS_LABEL_PREFIX:-com.$USER}"
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Installing Python dependencies (markitdown + Google API clients)"
# --break-system-packages --user matches how the cron jobs find these tools.
python3 -m pip install --break-system-packages --user -r requirements.txt

echo "==> Verifying markitdown has document support"
if python3 -c "import pdfminer, openpyxl, pptx, mammoth" 2>/dev/null; then
  echo "    ok: pdf/xlsx/pptx/docx converters present"
else
  echo "    WARNING: markitdown extras missing, PDFs/DOCX/XLSX will fail to convert."
  echo "    Re-run: python3 -m pip install --break-system-packages --user 'markitdown[pdf,docx,xlsx,pptx]==0.1.7'"
fi

echo "==> Checking the LLM backend (tools/llm.py: claude CLI by default, or an OpenAI-compatible endpoint)"
if [ "${BRAINLESS_LLM_PROVIDER:-claude-cli}" = "openai-compatible" ]; then
  if [ -n "${BRAINLESS_LLM_BASE_URL:-}" ] && [ -n "${BRAINLESS_LLM_MODEL:-}" ]; then
    echo "    ok: openai-compatible endpoint $BRAINLESS_LLM_BASE_URL model $BRAINLESS_LLM_MODEL"
  else
    echo "    WARNING: BRAINLESS_LLM_PROVIDER=openai-compatible but BRAINLESS_LLM_BASE_URL / BRAINLESS_LLM_MODEL are not set."
  fi
elif command -v claude >/dev/null 2>&1 || ls "$HOME"/.nvm/versions/node/*/bin/claude >/dev/null 2>&1; then
  echo "    ok: claude CLI found (run 'claude' once to sign in; jobs fail with an auth error otherwise)"
else
  echo "    WARNING: claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code"
  echo "    or set BRAINLESS_LLM_PROVIDER=openai-compatible with BRAINLESS_LLM_BASE_URL and BRAINLESS_LLM_MODEL."
fi


echo "==> Checking python3 (there is no unversioned 'python' on Homebrew 3.14; all docs use python3)"
if command -v python3 >/dev/null 2>&1; then
  echo "    ok: python3 -> $(command -v python3) ($(python3 -V 2>&1))"
else
  echo "    ERROR: python3 not found on PATH."
fi

cat <<'EOF'

==> Automation is launchd, NOT cron. The plists live in ~/Library/LaunchAgents/
    and are named ${PREFIX}.brainless.<job>.plist. A trailing `.disabled`
    on the filename means the job is parked.

  Active jobs:
    ${PREFIX}.brainless.hourly    StartInterval 3600, runs .agents/scripts/cron_wrapper.sh
    ${PREFIX}.brainless.inbox     WatchPaths Inbox/, runs .agents/scripts/inbox_watch_wrapper.sh
    ${PREFIX}.brainless.backup    daily 21:30, runs .agents/scripts/vault_backup.sh

  Load or reload one:
    launchctl bootout  gui/$UID/${PREFIX}.brainless.hourly 2>/dev/null
    launchctl bootstrap gui/$UID ~/Library/LaunchAgents/${PREFIX}.brainless.hourly.plist

  Fire one immediately (useful after a fix):
    launchctl kickstart -k gui/$UID/${PREFIX}.brainless.hourly

  Check status (last exit code should be 0):
    launchctl list | grep brainless

  Enable a parked job: drop the `.disabled` suffix, then bootstrap it as above.

Done.
EOF
