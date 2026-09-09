#!/usr/bin/env bash
# brainless installer. One line from a fresh machine:
#
#   curl -fsSL https://raw.githubusercontent.com/ezapmar/brainless-public/main/install.sh | bash
#
# Or with options (run the script directly):
#   bash install.sh --vault ~/brainless --schedule
#
# What it does, idempotently:
#   1. checks git and python3 (3.10+)
#   2. clones the engine into the vault directory (or pulls if it is already there)
#   3. creates .venv and installs the core Python dependency (markitdown)
#   4. checks the LLM backend: the claude CLI, or an OpenAI-compatible endpoint from env
#   5. asks for your name and output language and writes _Agent-Context/PROFILE.md
#   6. installs the `brainless` command into ~/.local/bin
#   7. with --schedule: installs the hourly, nightly and weekly jobs (launchd on macOS,
#      systemd user timers on Linux)
#   8. runs a dry compile as a smoke test
#
# Nothing here touches files outside the vault, ~/.local/bin, ~/.config/brainless and,
# with --schedule, ~/Library/LaunchAgents or ~/.config/systemd/user.
set -euo pipefail

REPO="${BRAINLESS_REPO:-https://github.com/ezapmar/brainless-public.git}"
VAULT="${BRAINLESS_VAULT:-$HOME/brainless}"
SCHEDULE=0
NONINTERACTIVE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --vault) VAULT="$2"; shift 2 ;;
    --repo) REPO="$2"; shift 2 ;;
    --schedule) SCHEDULE=1; shift ;;
    --yes) NONINTERACTIVE=1; shift ;;
    -h|--help) sed -n 2,20p "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
VAULT="${VAULT/#\~/$HOME}"

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '    WARNING: %s\n' "$*"; }
ok() { printf '    ok: %s\n' "$*"; }

# 1. prerequisites
say "Checking prerequisites"
command -v git >/dev/null 2>&1 || { echo "git is required" >&2; exit 1; }
PY=""
for cand in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then PY="$cand"; break; fi
done
[ -n "$PY" ] || { echo "python3 3.10 or newer is required" >&2; exit 1; }
ok "git $(git --version | awk '{print $3}'), $PY $($PY -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"

# 2. clone or update
if [ -d "$VAULT/.git" ]; then
  say "Updating existing vault at $VAULT"
  git -C "$VAULT" pull --rebase --autostash --quiet || warn "pull failed; continuing with the local copy"
elif [ -e "$VAULT" ] && [ -n "$(ls -A "$VAULT" 2>/dev/null)" ]; then
  echo "$VAULT exists and is not a brainless checkout; pass --vault <empty or existing vault dir>" >&2; exit 1
else
  say "Cloning into $VAULT"
  git clone --quiet "$REPO" "$VAULT"
fi
cd "$VAULT"

# 3. venv + core dependency
say "Python environment (.venv)"
if [ ! -x .venv/bin/python ]; then "$PY" -m venv .venv; fi
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements-core.txt
if .venv/bin/python -c "import pdfminer, openpyxl, pptx, mammoth" 2>/dev/null; then
  ok "markitdown with pdf, docx, xlsx, pptx converters"
else
  warn "markitdown document extras missing; PDF and Office conversion will fail until fixed"
fi

# 4. LLM backend
say "LLM backend"
if [ "${BRAINLESS_LLM_PROVIDER:-claude-cli}" = "openai-compatible" ]; then
  if [ -n "${BRAINLESS_LLM_BASE_URL:-}" ] && [ -n "${BRAINLESS_LLM_MODEL:-}" ]; then
    ok "openai-compatible endpoint $BRAINLESS_LLM_BASE_URL, model $BRAINLESS_LLM_MODEL"
  else
    warn "BRAINLESS_LLM_PROVIDER=openai-compatible needs BRAINLESS_LLM_BASE_URL and BRAINLESS_LLM_MODEL"
  fi
elif command -v claude >/dev/null 2>&1; then
  ok "claude CLI found; run 'claude' once to sign in if you have not"
else
  warn "no LLM backend. Install the claude CLI (npm install -g @anthropic-ai/claude-code) and sign in,"
  warn "or export BRAINLESS_LLM_PROVIDER=openai-compatible with BRAINLESS_LLM_BASE_URL and BRAINLESS_LLM_MODEL."
fi

# 5. profile
PROFILE="_Agent-Context/PROFILE.md"
if grep -q '^owner_name: the owner' "$PROFILE" 2>/dev/null; then
  say "Owner profile"
  NAME="the owner"; LANG_CODE="en"
  if [ "$NONINTERACTIVE" -eq 0 ] && [ -t 0 ]; then
    read -r -p "    Your first name (used in prompts): " NAME_IN; NAME="${NAME_IN:-$NAME}"
    read -r -p "    Output language, en or tr [en]: " LANG_IN; LANG_CODE="${LANG_IN:-en}"
  fi
  case "$LANG_CODE" in tr|en) ;; *) LANG_CODE="en" ;; esac
  # Escape for sed replacement.
  NAME_ESC="$(printf '%s' "$NAME" | sed 's/[&/\]/\\&/g')"
  sed -i.bak -e "s/^owner_name: .*/owner_name: $NAME_ESC/" -e "s/^owner_full_name: .*/owner_full_name: $NAME_ESC/" \
      -e "s/^output_lang: .*/output_lang: $LANG_CODE/" "$PROFILE" && rm -f "$PROFILE.bak"
  ok "PROFILE.md written for $NAME ($LANG_CODE). Edit it any time."
else
  ok "PROFILE.md already set"
fi

# 6. brainless command
say "Installing the brainless command"
mkdir -p "$HOME/.local/bin" "$HOME/.config/brainless"
printf '%s\n' "$VAULT" > "$HOME/.config/brainless/vault"
install -m 0755 bin/brainless "$HOME/.local/bin/brainless"
case ":$PATH:" in *":$HOME/.local/bin:"*) ok "brainless -> $HOME/.local/bin/brainless" ;;
  *) warn "add \$HOME/.local/bin to your PATH to use the brainless command" ;; esac

# 7. schedulers
if [ "$SCHEDULE" -eq 1 ]; then
  say "Installing scheduled jobs"
  if [ "$(uname -s)" = "Darwin" ]; then
    PREFIX="${BRAINLESS_LABEL_PREFIX:-com.$USER}"
    mkdir -p "$HOME/Library/LaunchAgents"
    write_plist() {  # label, program args (string), schedule xml
      local label="$1" args="$2" sched="$3" f="$HOME/Library/LaunchAgents/$label.plist"
      cat > "$f" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key><array>$args</array>
  <key>EnvironmentVariables</key><dict><key>BRAINLESS_VAULT</key><string>$VAULT</string></dict>
  <key>WorkingDirectory</key><string>$VAULT</string>
  $sched
  <key>StandardOutPath</key><string>$VAULT/logs/$label.log</string>
  <key>StandardErrorPath</key><string>$VAULT/logs/$label.log</string>
</dict></plist>
EOF
      launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
      launchctl bootstrap "gui/$(id -u)" "$f"
      ok "$label"
    }
    mkdir -p logs
    write_plist "$PREFIX.brainless.hourly" "<string>/bin/bash</string><string>$VAULT/.agents/scripts/cron_wrapper.sh</string>" "<key>StartInterval</key><integer>3600</integer>"
    write_plist "$PREFIX.brainless.nightly" "<string>$VAULT/.venv/bin/python</string><string>$VAULT/tools/nightly_processor.py</string>" "<key>StartCalendarInterval</key><dict><key>Hour</key><integer>23</integer><key>Minute</key><integer>0</integer></dict>"
    write_plist "$PREFIX.brainless.lint" "<string>$VAULT/.venv/bin/python</string><string>$VAULT/tools/lint_wiki.py</string><string>--fix</string><string>--fix-links</string>" "<key>StartCalendarInterval</key><dict><key>Weekday</key><integer>0</integer><key>Hour</key><integer>22</integer><key>Minute</key><integer>0</integer></dict>"
  else
    bash .agents/systemd/install.sh
  fi
fi

# 8. smoke test
say "Smoke test (dry compile, no LLM calls)"
BRAINLESS_VAULT="$VAULT" .venv/bin/python tools/compile_resources.py --dry-run --only summaries | tail -1

cat <<EOF

brainless is installed at $VAULT

  brainless help               all commands
  brainless compile            build the .wiki layer from your notes
  brainless search "<query>"   search the compiled wiki
  brainless dialectic "<thesis>"  five personas argue it, locally
  brainless calibrate          decisions due for grading

Drop notes into Inbox/ and Thinking/Daily/, beliefs into Thinking/Beliefs/, decisions
into Thinking/Decisions/ (templates in _Templates/). Re-run this installer any time; it
is safe. Add --schedule to install the hourly, nightly and weekly jobs.
EOF
