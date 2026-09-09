#!/bin/bash
# Gorev hatti (worker, 30 dk): Spiky aksiyon cikarimi + Google Tasks senkronu.
# Sirasi onemli: once cikarim (yerel yeni maddeler), sonra iki yonlu senkron,
# sonra commit+push. gtasks venv'i yoksa senkron adimi sessizce atlanir.
set -u
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
export MISE_QUIET=1
VAULT="${BRAINLESS_VAULT:-$HOME/projects/brainless}"
GPY="$HOME/.local/venvs/gtasks/bin/python"
cd "$VAULT" || exit 1

git pull --rebase --autostash --quiet || true
python3 .agents/scripts/spiky_actions.py "$@"
[ -x "$GPY" ] && "$GPY" .agents/scripts/gtasks_sync.py
bash .agents/scripts/worker_backup.sh
