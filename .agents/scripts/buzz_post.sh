#!/usr/bin/env bash
# Persist every notification before delivery. A nonzero exit means enqueue failed.
set -eu
VAULT="${BRAINLESS_VAULT:-$HOME/projects/brainless}"
export PYTHONPATH="$VAULT/tools:$VAULT/.agents/scripts${PYTHONPATH:+:$PYTHONPATH}"
exec python3 "$VAULT/tools/buzz_delivery.py" "$@"
