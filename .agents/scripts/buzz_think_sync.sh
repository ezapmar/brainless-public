#!/usr/bin/env bash
# Buzz Layer 1: post today's thinking surface (THINKING.md) to #thinking once,
# as the "thinking" identity. Runs as ExecStartPost of brainless-think-surface,
# after think_surface.py has regenerated the file. Idempotent via a state file.
# Never fails the caller: buzz_post.sh swallows all errors and exits 0.
set -u
VAULT="${BRAINLESS_VAULT:-$HOME/projects/brainless}"
STATE="$VAULT/.agents/state/buzz_think_posted"
TODAY="$(date +%F)"
FILE="$VAULT/_Agent-Context/THINKING.md"
[ -s "$FILE" ] || exit 0
grep -qx "$TODAY" "$STATE" 2>/dev/null && exit 0
BODY="$(cat "$FILE")"
[ -n "$(printf '%s' "$BODY" | tr -d '[:space:]')" ] || exit 0
if printf '%s\n' "$BODY" | "$VAULT/.agents/scripts/buzz_post.sh" thinking thinking 2>&1 | grep -q "posted as"; then
  mkdir -p "$(dirname "$STATE")"; echo "$TODAY" >> "$STATE"
fi
exit 0
