#!/usr/bin/env bash
# Post stdin (markdown) to a Buzz channel as a named brainless identity.
# Usage: some_command | buzz_post.sh <identity> <channel-name>
# Identities live in ~/.config/brainless/buzz/keys/<identity> (buzz-admin generate-key output).
# Channel ids are resolved by name via `buzz channels list` and cached in
# ~/.config/brainless/buzz/channels.json. Never fails the caller: any error
# is logged to stderr and the exit code is 0, so Telegram and git flows are untouched.
set -u
IDENTITY="${1:-}"; CHANNEL="${2:-}"
BUZZ_DIR="$HOME/.config/brainless/buzz"
KEYFILE="$BUZZ_DIR/keys/$IDENTITY"
# Relay URL: env wins, then ~/.config/brainless/buzz/relay_url, then the pinned pilot address.
# The relay resolves its community from the Host header (name and port), so the
# MagicDNS name must be used even from the relay host itself, never 127.0.0.1.
if [ -z "${BUZZ_RELAY_URL:-}" ] && [ -s "$HOME/.config/brainless/buzz/relay_url" ]; then
  BUZZ_RELAY_URL="$(head -1 "$HOME/.config/brainless/buzz/relay_url")"
fi
export BUZZ_RELAY_URL="${BUZZ_RELAY_URL:-http://localhost:3000}"
export PATH="$HOME/.cargo/bin:$PATH"

log() { echo "[buzz_post] $(date '+%F %T') $*" >&2; }

if [ -z "$IDENTITY" ] || [ -z "$CHANNEL" ]; then log "usage: buzz_post.sh <identity> <channel>"; exit 0; fi
if ! command -v buzz >/dev/null 2>&1; then log "buzz cli not installed, skipping"; exit 0; fi
if [ ! -s "$KEYFILE" ]; then log "no key for identity '$IDENTITY', skipping"; exit 0; fi

BUZZ_PRIVATE_KEY="$(awk '/Secret key/{print $3}' "$KEYFILE")"
export BUZZ_PRIVATE_KEY
[ -n "$BUZZ_PRIVATE_KEY" ] || { log "empty key for '$IDENTITY'"; exit 0; }

BODY="$(cat)"
if [ -z "$(printf '%s' "$BODY" | tr -d '[:space:]')" ]; then log "empty body, nothing posted"; exit 0; fi

# Resolve channel id (cache first, then relay).
CACHE="$BUZZ_DIR/channels.json"
CID=""
if [ -s "$CACHE" ] && command -v jq >/dev/null 2>&1; then
  CID="$(jq -r --arg n "$CHANNEL" '.[$n] // empty' "$CACHE" 2>/dev/null)"
fi
if [ -z "$CID" ]; then
  LIST="$(buzz channels list 2>/dev/null)" || LIST=""
  if command -v jq >/dev/null 2>&1 && [ -n "$LIST" ]; then
    CID="$(printf '%s' "$LIST" | jq -r --arg n "$CHANNEL" '(if type=="array" then . else .channels end)[]? | select(.name==$n) | (.channel_id // .id)' 2>/dev/null | head -1)"
    if [ -n "$CID" ]; then
      TMP="$(mktemp)"; { [ -s "$CACHE" ] && cat "$CACHE" || echo '{}'; } | jq --arg n "$CHANNEL" --arg id "$CID" '.[$n]=$id' > "$TMP" && mv "$TMP" "$CACHE"
    fi
  fi
fi
if [ -z "$CID" ]; then log "channel '$CHANNEL' not found on relay"; exit 0; fi

# Relay limit is 512 KB per message; keep well under it.
MAX=60000
if [ "${#BODY}" -gt "$MAX" ]; then BODY="${BODY:0:$MAX}"$'\n\n'"[kesildi: mesaj $MAX karakteri asti]"; fi

if printf '%s' "$BODY" | timeout 30 buzz messages send --channel "$CID" --content - >/dev/null 2>"$BUZZ_DIR/last_error.log"; then
  log "posted as $IDENTITY to #$CHANNEL"
else
  log "send failed for #$CHANNEL: $(head -c 200 "$BUZZ_DIR/last_error.log")"
fi
exit 0
