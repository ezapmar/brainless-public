#!/usr/bin/env bash
# Install one conversational Buzz agent with its own channel on the always-on worker.
#   bash .agents/buzz/install_agent_channel.sh <slug>          # e.g. writing, narratives
# Reads the agent card .agents/buzz/agents/<slug>/agent.env (channel name, display
# name, drafts folder), generates a Buzz key (once), registers it as a relay member,
# creates the channel with the moderator identity if missing, caches its uuid in
# channels.json, adds the owner and the agent as members, assembles ~/buzz-<slug>/
# (system prompt with placeholders filled, Claude settings scoped to the drafts
# folder), writes the env file from agents/env.template, installs buzz-agent@.service
# and enables buzz-agent@<slug>.service. Idempotent: keys, channels and drafts are kept.
#
# Model and effort: BUZZ_AGENT_MODEL (default claude-fable-5-1) and BUZZ_AGENT_EFFORT
# (default high). The harness applies them to every new session (buzz-acp --model).
set -eu
SLUG="${1:?usage: install_agent_channel.sh <slug>}"
VAULT="${BRAINLESS_VAULT:-$HOME/projects/brainless}"
BUZZ_DIR="$HOME/.config/brainless/buzz"
KEYS="$BUZZ_DIR/keys"
RELAY_CONTAINER="${BUZZ_RELAY_CONTAINER:-buzz-prod-relay-1}"
CARD="$VAULT/.agents/buzz/agents/$SLUG/agent.env"
[ -s "$CARD" ] || { echo "no agent card at $CARD" >&2; exit 1; }
# shellcheck disable=SC1090
. "$CARD"
MODEL="${BUZZ_AGENT_MODEL:-claude-fable-5-1}"
EFFORT="${BUZZ_AGENT_EFFORT:-high}"
export PATH="$HOME/.cargo/bin:$PATH"
BUZZ_BIN="$(command -v buzz || echo "$HOME/.cargo/bin/buzz")"
if [ -s "$BUZZ_DIR/relay_url" ]; then export BUZZ_RELAY_URL="$(head -1 "$BUZZ_DIR/relay_url")"; fi
export BUZZ_RELAY_URL="${BUZZ_RELAY_URL:-http://localhost:3000}"
OWNER="$(grep -h '^BUZZ_ACP_AGENT_OWNER=' "$BUZZ_DIR/assistant.env" | cut -d= -f2)"
AGENT_COMMAND="$(grep -h '^BUZZ_ACP_AGENT_COMMAND=' "$BUZZ_DIR/assistant.env" | cut -d= -f2)"
CLAUDE_EXE="$(grep -h '^CLAUDE_CODE_EXECUTABLE=' "$BUZZ_DIR/assistant.env" | cut -d= -f2)"
[ -n "$OWNER" ] && [ -n "$AGENT_COMMAND" ] || { echo "assistant.env must define the owner pubkey and the agent command" >&2; exit 1; }

log() { echo "[install_agent_channel] $*" >&2; }
profile() { grep -m1 "^$1:" "$VAULT/_Agent-Context/PROFILE.md" 2>/dev/null | cut -d: -f2- | xargs || true; }
pubkey_of() { awk '/Public key/{print $3}' "$KEYS/$1"; }
secret_of() { awk '/Secret key/{print $3}' "$KEYS/$1"; }
ensure_key() {
  local f="$KEYS/$1"
  [ -s "$f" ] && return
  mkdir -p "$KEYS"; chmod 700 "$KEYS"
  docker exec "$RELAY_CONTAINER" buzz-admin generate-key > "$f"
  chmod 600 "$f"
  log "generated key for $1"
}

OWNER_NAME="$(profile owner_name)"; OWNER_NAME="${OWNER_NAME:-the owner}"
EDITOR_DIR="$(profile editor_dir)"; EDITOR_DIR="${EDITOR_DIR:-Writings/Editor}"
NARRATIVES_DIR="$(profile narratives_dir)"; NARRATIVES_DIR="${NARRATIVES_DIR:-Writings/Narratives}"
DRAFTS_DIR="$(profile "${DRAFTS_DIR_KEY:-drafts_dir}")"; DRAFTS_DIR="${DRAFTS_DIR:-$DRAFTS_DIR_DEFAULT}"
[ -n "${DRAFTS_SUBDIR:-}" ] && DRAFTS_DIR="$DRAFTS_DIR/$DRAFTS_SUBDIR"
LANG_NAME="$(cd "$VAULT/tools" && python3 -c 'import owner_profile as o; print(o.lang_name())' 2>/dev/null || echo English)"
mkdir -p "$VAULT/$DRAFTS_DIR"

[ -s "$KEYS/moderator" ] || { log "moderator key missing - run install_personas.sh first"; exit 1; }
MOD="$(secret_of moderator)"
ensure_key "$SLUG"
PUB="$(pubkey_of "$SLUG")"
docker exec "$RELAY_CONTAINER" buzz-admin add-member --pubkey "$PUB" >/dev/null 2>&1 || true
BUZZ_PRIVATE_KEY="$(secret_of "$SLUG")" buzz users set-profile --name "$DISPLAY_NAME" >/dev/null 2>&1 || log "profile name not set"

channel_id() {
  BUZZ_PRIVATE_KEY="$MOD" buzz channels list 2>/dev/null \
    | jq -r --arg n "$CHANNEL" '(if type=="array" then . else .channels end)[]? | select(.name==$n) | (.channel_id // .id)' | head -1
}
CID="$(channel_id || true)"
if [ -z "$CID" ]; then
  BUZZ_PRIVATE_KEY="$MOD" buzz channels create --name "$CHANNEL" --type stream --visibility open --description "$DESCRIPTION" >/dev/null
  CID="$(channel_id)"; log "created #$CHANNEL ($CID)"
fi
[ -n "$CID" ] || { log "channel id not found"; exit 1; }
TMP="$(mktemp)"; { [ -s "$BUZZ_DIR/channels.json" ] && cat "$BUZZ_DIR/channels.json" || echo '{}'; } \
  | jq --arg n "$CHANNEL" --arg id "$CID" '.[$n]=$id' > "$TMP" && mv "$TMP" "$BUZZ_DIR/channels.json"
BUZZ_PRIVATE_KEY="$MOD" buzz channels add-member --channel "$CID" --pubkey "$OWNER" --role owner >/dev/null 2>&1 || true
BUZZ_PRIVATE_KEY="$MOD" buzz channels add-member --channel "$CID" --pubkey "$PUB" --role bot >/dev/null 2>&1 || true

dir="$HOME/buzz-$SLUG"; mkdir -p "$dir/.claude"
sed -e "s|{{OWNER}}|$OWNER_NAME|g" -e "s|{{VAULT}}|$VAULT|g" -e "s|{{EDITOR_DIR}}|$EDITOR_DIR|g" \
    -e "s|{{DRAFTS_DIR}}|$DRAFTS_DIR|g" -e "s|{{NARRATIVES_DIR}}|$NARRATIVES_DIR|g" \
    -e "s|{{BUZZ_BIN}}|$BUZZ_BIN|g" -e "s|{{LANG}}|$LANG_NAME|g" \
    "$VAULT/.agents/buzz/agents/$SLUG/system_prompt.md" > "$dir/system_prompt.md"
sed -e "s|__VAULT__|$VAULT|g" -e "s|__DRAFTS_DIR__|$DRAFTS_DIR|g" -e "s|__EDITOR_DIR__|$EDITOR_DIR|g" \
    -e "s|__BUZZ_BIN__|$BUZZ_BIN|g" -e "s|__HOME__|$HOME|g" \
    "$VAULT/.agents/buzz/agents/settings.json" > "$dir/.claude/settings.json"

env="$BUZZ_DIR/$SLUG.env"
sed -e "s|__SECRET__|$(secret_of "$SLUG")|" -e "s|__OWNER_PUBKEY__|$OWNER|" -e "s|__RELAY_URL__|${BUZZ_RELAY_URL/https:/wss:}|" \
    -e "s|__AGENT_COMMAND__|$AGENT_COMMAND|" -e "s|__CLAUDE_EXE__|${CLAUDE_EXE:-claude}|" \
    -e "s|__DISPLAY_NAME__|$DISPLAY_NAME|" -e "s|__SLUG__|$SLUG|g" -e "s|__HOME__|$HOME|g" \
    -e "s|__CHANNEL_UUID__|$CID|" -e "s|__MODEL__|$MODEL|" -e "s|__EFFORT__|$EFFORT|" -e "s|__MAX_TURN__|${MAX_TURN:-1500}|" \
    "$VAULT/.agents/buzz/agents/env.template" > "$env"
chmod 600 "$env"

mkdir -p ~/.config/systemd/user
cp -f "$VAULT/.agents/systemd/buzz-agent@.service" ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now "buzz-agent@$SLUG.service"
systemctl --user restart "buzz-agent@$SLUG.service"
log "done: #$CHANNEL ($CID), agent $DISPLAY_NAME ($PUB), drafts $DRAFTS_DIR, model $MODEL/$EFFORT"
