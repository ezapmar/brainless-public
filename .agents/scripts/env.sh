#!/bin/bash
# Shared environment for the four launchd jobs (capture, hourly, night, llm).
# Sourced, never run. One Mac, one vault, one place to change a path.
VAULT="${BRAINLESS_VAULT:-$HOME/projects/brainless}"
cd "$VAULT" || exit 1
export BRAINLESS_VAULT="$VAULT"

# launchd starts with a bare PATH. Homebrew first: its python3 is the one with
# the engine's packages; Apple's has none. User script and node dirs are found
# by glob so a version bump does not break the jobs.
PY_BINS=$(ls -d "$HOME"/Library/Python/*/bin 2>/dev/null | sort -Vr | tr '\n' ':')
NODE_BIN=$(ls -d "$HOME"/.nvm/versions/node/*/bin 2>/dev/null | sort -V | tail -1)
export PATH="/opt/homebrew/bin:${PY_BINS}$HOME/.local/bin:${NODE_BIN:+$NODE_BIN:}/usr/local/bin:/usr/bin:/bin"
export PYTHONPATH="$VAULT/tools:$VAULT/.agents/scripts${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1
mkdir -p logs

# Every third-party package lives in one Python 3.13 venv: search (fastembed),
# the Google client libraries and markitdown. onnxruntime has no wheel for
# Homebrew's newest Python, which is why the semantic index went stale for a
# month, and nothing is installed into the system Python. Create it with
#   python3.13 -m venv ~/.local/venvs/brainless
#   ~/.local/venvs/brainless/bin/pip install -r requirements-core.txt \
#       -r requirements-search.txt -r requirements-google.txt
GPY="$HOME/.local/venvs/brainless/bin/python"
[ -x "$GPY" ] && export BRAINLESS_SEARCH_PY="$GPY"

# Local model (Gemma 4 12B on llama-server, the "llm" launchd job). Lanes listed
# here run on it; every other lane stays on the Claude CLI subscription. A local
# lane never falls back to Claude (tools/llm.py refuses it unless told), so a
# stopped server means the job fails and retries, not that text leaves the Mac.
export BRAINLESS_LLM_BASE_URL="http://127.0.0.1:8080/v1"
export BRAINLESS_LLM_MODEL="gemma-4-12b"
for lane in NOTE_CLASSIFY DREAMING_JUDGE COMPILE_ALIASES CAPTURE_NOTE CAPTURE_PHOTO SPIKY_ACTIONS ASK; do
  export "BRAINLESS_LLM_PROVIDER_${lane}=openai-compatible"
done

# run <job-label> <command...>: one line in the run log, output in logs/<label>.log
run() {
  local label="$1"; shift
  python3 tools/run_log.py exec -- "$@" >> "logs/$label.log" 2>&1
}

rotate() {  # keep each log under 5 MB, one previous generation
  local f
  for f in logs/*.log; do
    [ -f "$f" ] && [ "$(stat -f%z "$f" 2>/dev/null || echo 0)" -gt $((5 * 1024 * 1024)) ] && mv -f "$f" "$f.1"
  done
  return 0
}
