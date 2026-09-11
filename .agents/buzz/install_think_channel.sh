#!/usr/bin/env bash
# Create the Buzz identity + channel for the daily thinking surface.
#   bash .agents/buzz/install_think_channel.sh
# Generates the "thinking" key (once), registers it as a relay member, creates
# the #thinking channel with the moderator identity if missing, caches its uuid
# in channels.json, and adds the owner + thinking as members. Idempotent: an
# existing key or channel is kept. Mirrors install_personas.sh.
set -eu
VAULT="${BRAINLESS_VAULT:-$HOME/projects/brainless}"
BUZZ_DIR="$HOME/.config/brainless/buzz"
KEYS="$BUZZ_DIR/keys"
RELAY_CONTAINER="${BUZZ_RELAY_CONTAINER:-buzz-prod-relay-1}"
CHANNEL_NAME="${THINKING_CHANNEL:-thinking}"
IDENTITY="thinking"
OWNER="$(grep -h '^BUZZ_ACP_AGENT_OWNER=' "$BUZZ_DIR/assistant.env" | cut -d= -f2)"
export PATH="$HOME/.cargo/bin:$PATH"
if [ -s "$BUZZ_DIR/relay_url" ]; then export BUZZ_RELAY_URL="$(head -1 "$BUZZ_DIR/relay_url")"; fi
export BUZZ_RELAY_URL="${BUZZ_RELAY_URL:-http://localhost:3000}"

log() { echo "[install_think_channel] $*" >&2; }
pubkey_of() { awk '/Public key/{print $3}' "$KEYS/$1"; }
secret_of() { awk '/Secret key/{print $3}' "$KEYS/$1"; }

ensure_key() {  # $1 = identity
  local f="$KEYS/$1"
  [ -s "$f" ] && return
  mkdir -p "$KEYS"; chmod 700 "$KEYS"
  docker exec "$RELAY_CONTAINER" buzz-admin generate-key > "$f"
  chmod 600 "$f"
  log "generated key for $1"
}

# The moderator identity (script owner of channels) must already exist; it is
# created by install_personas.sh. Fail loudly if it is missing.
[ -s "$KEYS/moderator" ] || { log "moderator key missing - run install_personas.sh first"; exit 1; }

ensure_key "$IDENTITY"
DUS_PUB="$(pubkey_of "$IDENTITY")"
docker exec "$RELAY_CONTAINER" buzz-admin add-member --pubkey "$DUS_PUB" >/dev/null 2>&1 || true
BUZZ_PRIVATE_KEY="$(secret_of "$IDENTITY")" buzz users set-profile --name "Thinking" >/dev/null 2>&1 \
  || log "thinking profile name not set (check: buzz users set-profile --help)"

channel_id() {
  BUZZ_PRIVATE_KEY="$(secret_of moderator)" buzz channels list 2>/dev/null \
    | jq -r --arg n "$CHANNEL_NAME" '(if type=="array" then . else .channels end)[]? | select(.name==$n) | (.channel_id // .id)' | head -1
}
CID="$(channel_id || true)"
if [ -z "$CID" ]; then
  BUZZ_PRIVATE_KEY="$(secret_of moderator)" buzz channels create --name "$CHANNEL_NAME" --type stream --visibility open \
    --description "Daily thinking surface: cadence step, decisions to grade, one provocation, resurfaced notes" >/dev/null
  CID="$(channel_id)"
  log "created #$CHANNEL_NAME ($CID)"
fi
[ -n "$CID" ] || { log "channel id not found"; exit 1; }

TMP="$(mktemp)"; { [ -s "$BUZZ_DIR/channels.json" ] && cat "$BUZZ_DIR/channels.json" || echo '{}'; } \
  | jq --arg n "$CHANNEL_NAME" --arg id "$CID" '.[$n]=$id' > "$TMP" && mv "$TMP" "$BUZZ_DIR/channels.json"

BUZZ_PRIVATE_KEY="$(secret_of moderator)" buzz channels add-member --channel "$CID" --pubkey "$OWNER" --role owner >/dev/null 2>&1 || true
BUZZ_PRIVATE_KEY="$(secret_of moderator)" buzz channels add-member --channel "$CID" --pubkey "$DUS_PUB" --role bot >/dev/null 2>&1 || true
log "done: #$CHANNEL_NAME ready, thinking is a member"
