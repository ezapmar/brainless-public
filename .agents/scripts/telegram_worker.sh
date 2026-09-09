#!/bin/bash
# 7/24 Linux worker (worker): Telegram capture + push.
# Mac ana yazar kalir; bu makine yalnizca Thinking/Daily'ye yazar ve push'lar.
# systemd timer'i 2 dakikada bir cagirir (brainless-telegram.timer).
set -u
WORKER="$(grep -m1 '^worker_name:' "${BRAINLESS_VAULT:-$HOME/projects/brainless}/_Agent-Context/PROFILE.md" 2>/dev/null | cut -d: -f2- | xargs)"; WORKER="${WORKER:-worker}"
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
export MISE_QUIET=1  # mise'nin bilgi satirlari claude stdout'una karisip nota siziyor
VAULT="${BRAINLESS_VAULT:-$HOME/projects/brainless}"
cd "$VAULT" || exit 1

git pull --rebase --quiet || true
python3 .agents/scripts/telegram_capture.py

git add "Thinking" "Inbox" ".wiki/digests/queries" 2>/dev/null  # dusunme dongusu Thinking/ altina yazar
if ! git diff --cached --quiet; then
  git commit --quiet -m "telegram capture: $(date +%F-%H%M) ($WORKER)"
  git pull --rebase --quiet && git push --quiet
fi
