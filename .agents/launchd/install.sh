#!/bin/bash
# Install the four launchd jobs that run brainless on this Mac, and retire the
# old ones. Safe to run again: each job is unloaded, rewritten and reloaded.
#
#   capture  every 2 min   Telegram poller + replies          (capture.sh)
#   hourly   every hour    CRM, Spiky, Tasks, morning set     (cron_wrapper.sh)
#   night    23:00 daily   compile chain + one commit/push    (night.sh)
#   llm      always on     Gemma 4 12B on llama-server :8080  (local lanes)
#
# launchd runs a StartCalendarInterval job that was missed while the Mac
# slept as soon as it wakes, so a closed lid costs a delay, not a night.
# Usage: bash .agents/launchd/install.sh [--uninstall]
set -euo pipefail
VAULT="${BRAINLESS_VAULT:-$HOME/projects/brainless}"
AGENTS="$HOME/Library/LaunchAgents"
PREFIX="local.brainless"
MODEL="${BRAINLESS_LOCAL_MODEL:-$HOME/models/gemma-4-12b/gemma-4-12b-it-qat-q4_0.gguf}"
MMPROJ="${BRAINLESS_LOCAL_MMPROJ:-$HOME/models/gemma-4-12b/mmproj-gemma-4-12b-it-qat-q4_0.gguf}"
LLAMA="$(command -v llama-server || echo /opt/homebrew/bin/llama-server)"
UID_N="$(id -u)"
mkdir -p "$AGENTS" "$VAULT/logs"

unload() { launchctl bootout "gui/$UID_N/$1" 2>/dev/null || true; }

# Jobs from the two-machine era. Moved aside, not deleted, so they can be read.
RETIRED="$AGENTS/retired-brainless"
for old in inbox backup closeout dashboard lint nightly reconcile resurface telegram; do
  for f in "$AGENTS/$PREFIX.$old.plist" "$AGENTS/$PREFIX.$old.plist.disabled"; do
    if [ -e "$f" ]; then
      unload "$PREFIX.$old"
      mkdir -p "$RETIRED"
      mv -f "$f" "$RETIRED/"
    fi
  done
done

plist() {  # plist <name> <schedule-xml> <program-args-xml>
  local name="$1" schedule="$2" args="$3" path="$AGENTS/$PREFIX.$1.plist"
  unload "$PREFIX.$name"
  [ "${UNINSTALL:-0}" = 1 ] && { rm -f "$path"; return; }
  cat > "$path" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$PREFIX.$name</string>
  <key>ProgramArguments</key><array>$args</array>
  <key>WorkingDirectory</key><string>$VAULT</string>
  <key>EnvironmentVariables</key><dict><key>BRAINLESS_VAULT</key><string>$VAULT</string></dict>
  $schedule
  <key>StandardOutPath</key><string>$VAULT/logs/launchd-$name.log</string>
  <key>StandardErrorPath</key><string>$VAULT/logs/launchd-$name.log</string>
  <key>ProcessType</key><string>Background</string>
</dict>
</plist>
EOF
  plutil -lint -s "$path"
  launchctl bootstrap "gui/$UID_N" "$path"
}

[ "${1:-}" = "--uninstall" ] && UNINSTALL=1
job() { echo "<string>/bin/bash</string><string>$VAULT/.agents/scripts/$1</string>"; }

plist capture '<key>StartInterval</key><integer>120</integer><key>RunAtLoad</key><true/>' "$(job capture.sh)"
plist hourly  '<key>StartInterval</key><integer>3600</integer><key>RunAtLoad</key><true/>' "$(job cron_wrapper.sh)"
plist night   '<key>StartCalendarInterval</key><dict><key>Hour</key><integer>23</integer><key>Minute</key><integer>0</integer></dict>' "$(job night.sh)"
if [ -f "$MODEL" ]; then
  plist llm '<key>KeepAlive</key><true/><key>RunAtLoad</key><true/>' \
    "<string>$LLAMA</string><string>-m</string><string>$MODEL</string><string>--mmproj</string><string>$MMPROJ</string><string>--alias</string><string>gemma-4-12b</string><string>--host</string><string>127.0.0.1</string><string>--port</string><string>8080</string><string>-c</string><string>32768</string><string>-np</string><string>1</string><string>--reasoning</string><string>off</string>"
else
  echo "model not found at $MODEL: llm job skipped, local lanes will fail until it exists"
fi

[ "${UNINSTALL:-0}" = 1 ] && echo "brainless launchd jobs removed" && exit 0
launchctl list | grep "$PREFIX" || true
