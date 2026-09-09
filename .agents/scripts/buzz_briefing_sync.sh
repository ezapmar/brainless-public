#!/usr/bin/env bash
# Buzz Katman 1: post today's morning briefing to #daily once (Brifing identity).
# Runs after every worker pull (worker_backup.sh). Idempotent via a state file.
# Posts only the part before the evening close-out marker; the close-out itself is
# posted by tools/evening_closeout.py when it is written.
set -u
VAULT="${BRAINLESS_VAULT:-$HOME/projects/brainless}"
STATE="$VAULT/.agents/state/buzz_briefing_posted"
TODAY="$(date +%F)"
FILE="$VAULT/Daily Briefings/daily-briefing-$TODAY.md"
MARKER="## Akşam Kapanışı"
[ -s "$FILE" ] || exit 0
grep -qx "$TODAY" "$STATE" 2>/dev/null && exit 0
# Skip files that only exist because the close-out opened them (no morning briefing).
grep -q "Sabah brifingi bugün üretilmedi" "$FILE" && exit 0
BODY="$(awk -v m="$MARKER" 'index($0,m)==1{exit} {print}' "$FILE")"
[ -n "$(printf '%s' "$BODY" | tr -d '[:space:]')" ] || exit 0
if printf '%s\n' "$BODY" | "$VAULT/.agents/scripts/buzz_post.sh" brifing daily 2>&1 | grep -q "posted as"; then
  mkdir -p "$(dirname "$STATE")"; echo "$TODAY" >> "$STATE"
fi
exit 0
