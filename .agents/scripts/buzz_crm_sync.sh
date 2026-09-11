#!/usr/bin/env bash
# Buzz Layer 1: post the CRM snapshot (_Agent-Context/CRM.md) to #crm as the
# "crm" identity whenever its body changed since the last post. Timestamps are
# ignored in the comparison, so a quiet hour posts nothing. Runs right after
# crm_capture.py (cron_wrapper.sh on the laptop, ExecStartPost on the worker).
# Never fails the caller: buzz_post.sh swallows all errors and exits 0.
set -u
VAULT="${BRAINLESS_VAULT:-$HOME/projects/brainless}"
STATE="$VAULT/.agents/state/buzz_crm_posted"
FILE="$VAULT/_Agent-Context/CRM.md"
[ -s "$FILE" ] || exit 0
BODY="$(cat "$FILE")"
[ -n "$(printf '%s' "$BODY" | tr -d '[:space:]')" ] || exit 0
HASH="$(printf '%s' "$BODY" | sed -E 's/[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}//g' | shasum -a 256 | cut -d' ' -f1)"
[ -s "$STATE" ] && [ "$(head -1 "$STATE")" = "$HASH" ] && exit 0
if printf '%s\n' "$BODY" | "$VAULT/.agents/scripts/buzz_post.sh" crm crm 2>&1 | grep -q "posted as"; then
  mkdir -p "$(dirname "$STATE")"; printf '%s\n%s\n' "$HASH" "$(date '+%F %T')" > "$STATE"
fi
exit 0
