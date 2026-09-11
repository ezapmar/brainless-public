#!/usr/bin/env bash
# Install the dialectic persona agents on the always-on worker.
#   bash .agents/buzz/install_personas.sh [slug ...]
# Default slugs: skeptic gambler scientist postmortem strategist.
#
# For each persona: generate a Buzz key (once), register it as a relay member,
# add it to the #dialectic channel, assemble ~/buzz-<slug>/ (system prompt =
# persona card + shared team rules, read-only Claude settings), write the env
# file from env.template, and enable buzz-persona@<slug>.service.
# Also creates the "moderator" key (script identity, no harness) and the
# #dialectic channel if missing. Idempotent: existing keys are kept.
set -eu
VAULT="${BRAINLESS_VAULT:-$HOME/projects/brainless}"
BUZZ_DIR="$HOME/.config/brainless/buzz"
KEYS="$BUZZ_DIR/keys"
RELAY_CONTAINER="${BUZZ_RELAY_CONTAINER:-buzz-prod-relay-1}"
CHANNEL_NAME="${DIALECTIC_CHANNEL:-dialectic}"
OWNER="$(grep -h '^BUZZ_ACP_AGENT_OWNER=' "$BUZZ_DIR/assistant.env" | cut -d= -f2)"
# Owner display name for the persona prompts ({{OWNER}} placeholder), from PROFILE.md.
OWNER_NAME="$(grep -m1 '^owner_name:' "$VAULT/_Agent-Context/PROFILE.md" 2>/dev/null | cut -d: -f2- | xargs)"; OWNER_NAME="${OWNER_NAME:-the owner}"
export PATH="$HOME/.cargo/bin:$PATH"
if [ -s "$BUZZ_DIR/relay_url" ]; then export BUZZ_RELAY_URL="$(head -1 "$BUZZ_DIR/relay_url")"; fi
export BUZZ_RELAY_URL="${BUZZ_RELAY_URL:-http://localhost:3000}"

declare -A NAMES=( [skeptic]=Skeptic [gambler]=Gambler [scientist]=Scientist [postmortem]=Postmortem [strategist]=Strategist )
SLUGS=("$@"); [ ${#SLUGS[@]} -gt 0 ] || SLUGS=(skeptic gambler scientist postmortem strategist)

log() { echo "[install_personas] $*" >&2; }

ensure_key() {  # $1 = identity
  local f="$KEYS/$1"
  if [ -s "$f" ]; then return; fi
  mkdir -p "$KEYS"; chmod 700 "$KEYS"
  docker exec "$RELAY_CONTAINER" buzz-admin generate-key > "$f"
  chmod 600 "$f"
  log "generated key for $1"
}
pubkey_of() { awk '/Public key/{print $3}' "$KEYS/$1"; }
secret_of() { awk '/Secret key/{print $3}' "$KEYS/$1"; }
ensure_member() {  # $1 = pubkey
  docker exec "$RELAY_CONTAINER" buzz-admin add-member --pubkey "$1" >/dev/null 2>&1 || true
}

ensure_key moderator
MOD_PUB="$(pubkey_of moderator)"
ensure_member "$MOD_PUB"
# Give the script identity a display name so threads do not show a bare pubkey.
BUZZ_PRIVATE_KEY="$(secret_of moderator)" buzz users set-profile --name "Moderator" >/dev/null 2>&1 \
  || log "moderator profile name not set (check: buzz users set-profile --help)"

# Channel: create with the moderator identity if missing; cache uuid in channels.json.
channel_id() {
  BUZZ_PRIVATE_KEY="$(secret_of moderator)" buzz channels list 2>/dev/null \
    | jq -r --arg n "$CHANNEL_NAME" '(if type=="array" then . else .channels end)[]? | select(.name==$n) | (.channel_id // .id)' | head -1
}
CID="$(channel_id || true)"
if [ -z "$CID" ]; then
  BUZZ_PRIVATE_KEY="$(secret_of moderator)" buzz channels create --name "$CHANNEL_NAME" --type stream --visibility open \
    --description "Critical dialectic: the personas argue the day's captures" >/dev/null
  CID="$(channel_id)"
  log "created #$CHANNEL_NAME ($CID)"
fi
[ -n "$CID" ] || { log "channel id not found"; exit 1; }
TMP="$(mktemp)"; { [ -s "$BUZZ_DIR/channels.json" ] && cat "$BUZZ_DIR/channels.json" || echo '{}'; } \
  | jq --arg n "$CHANNEL_NAME" --arg id "$CID" '.[$n]=$id' > "$TMP" && mv "$TMP" "$BUZZ_DIR/channels.json"
BUZZ_PRIVATE_KEY="$(secret_of moderator)" buzz channels add-member --channel "$CID" --pubkey "$OWNER" --role owner >/dev/null 2>&1 || true

for slug in "${SLUGS[@]}"; do
  ensure_key "$slug"
  PUB="$(pubkey_of "$slug")"
  ensure_member "$PUB"
  BUZZ_PRIVATE_KEY="$(secret_of moderator)" buzz channels add-member --channel "$CID" --pubkey "$PUB" --role bot >/dev/null 2>&1 || true

  dir="$HOME/buzz-$slug"
  mkdir -p "$dir/.claude"
  { cat "$VAULT/.agents/buzz/personas/$slug/system_prompt.md"; echo; cat "$VAULT/.agents/buzz/team_instructions.md"; } | sed "s/{{OWNER}}/$OWNER_NAME/g" > "$dir/system_prompt.md"
  cp -f "$VAULT/.agents/buzz/personas/settings.json" "$dir/.claude/settings.json"

  env="$BUZZ_DIR/$slug.env"
  sed -e "s|__SECRET__|$(secret_of "$slug")|" -e "s|__OWNER_PUBKEY__|$OWNER|" -e "s|__RELAY_URL__|${BUZZ_RELAY_URL/https:/wss:}|" \
      -e "s|__DISPLAY_NAME__|${NAMES[$slug]:-$slug}|" -e "s|__SLUG__|$slug|" \
      -e "s|__MODERATOR_PUBKEY__|$MOD_PUB|" -e "s|__CHANNEL_UUID__|$CID|" \
      "$VAULT/.agents/buzz/personas/env.template" > "$env"
  chmod 600 "$env"
  log "persona $slug ready ($PUB)"
done

mkdir -p ~/.config/systemd/user
cp -f "$VAULT/.agents/systemd/buzz-persona@.service" ~/.config/systemd/user/
systemctl --user daemon-reload
for slug in "${SLUGS[@]}"; do systemctl --user enable --now "buzz-persona@$slug.service"; done
systemctl --user list-units --no-pager 'buzz-persona@*' || true
