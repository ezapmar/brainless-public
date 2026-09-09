#!/bin/bash
# Daily vault backup: commit everything and push to origin.
# Replaces the Obsidian Git plugin's "vault backup" commits, which silently
# stopped on 2026-07-21. Runs from launchd (<prefix>.brainless.backup).
cd "${BRAINLESS_VAULT:-$HOME/projects/brainless}" || exit 1

# 2026-09-03: iki gun boyunca lokal commit atildi ama push dustu. Log'da
# "ssh: connect to host github.com port 22" vardi: launchd job'i Mac uykudan
# uyanirken atesliyor, Wi-Fi henuz bagli degil. Watchdog bunu "Mac 63 saattir
# commit atmadi" diye raporladi. Asagidaki ag beklemesi ve push retry'i o
# senaryo icin.
github_reachable() {
  ssh -o BatchMode=yes -o ConnectTimeout=5 -T git@github.com 2>&1 \
    | grep -q "successfully authenticated"
}

wait_for_github() {
  local i
  for i in $(seq 1 12); do
    github_reachable && return 0
    sleep 5
  done
  return 1
}

# Rebase yarida kalmissa (onceki kosuda cakisma olmus) repo kilitli kalir ve
# sonraki her pull/push duser. Temizle ve haber ver, elle cozulsun.
if [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; then
  git rebase --abort || true
  osascript -e 'display notification "Yarim kalan rebase temizlendi, elle senkron gerekiyor" with title "brainless backup"' 2>/dev/null
  echo "$(date '+%Y-%m-%d %H:%M:%S') yarim kalan rebase abort edildi"
fi

if wait_for_github; then
  # Omarchy worker'in push'ladigi capture'lari al (2026-08-26'dan beri
  # Telegram dinleyicisi ofisteki Linux makinede calisiyor).
  git pull --rebase --autostash --quiet origin master || true
  if [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; then
    git rebase --abort || true
    osascript -e 'display notification "Pull cakismasi: elle senkron gerekiyor" with title "brainless backup"' 2>/dev/null
    echo "$(date '+%Y-%m-%d %H:%M:%S') pull cakismasi, rebase abort edildi"
  fi
else
  echo "$(date '+%Y-%m-%d %H:%M:%S') github erisilemiyor, pull atlandi"
fi

# Guard: GitHub hard-rejects blobs over 100 MB and a single oversized file
# poisons every subsequent push. Auto-ignore anything over 95 MB before adding.
find . -type f -size +95M -not -path "./.git/*" -not -path "./_Backup/*" -not -path "./venv/*" -print0 | while IFS= read -r -d '' f; do
  rel="${f#./}"
  if ! git check-ignore -q "$rel"; then
    echo "$rel" >> .gitignore
    # Dosya adini AppleScript'e SOKMA (tirnak/ters bolu enjeksiyonu); sabit metin.
    osascript -e 'display notification "95MB+ dosya gitignore edildi (log: vault_backup.log)" with title "brainless backup"' 2>/dev/null
    echo "95MB+ gitignore: $rel"
  fi
done

if [ -n "$(git status --porcelain)" ]; then
  git add -A
  git commit -m "vault backup: $(date '+%Y-%m-%d %H:%M:%S')" || true
fi

# Push pause: while .agents/state/no_push exists, back up locally only.
# (Set 2026-08-04: first push after history rewrite is ~2 GB; the owner will
# trigger it manually when on a suitable connection, then delete the flag.)
if [ -f .agents/state/no_push ]; then
  exit 0
fi

# Push regardless of whether this run committed; earlier commits may be unpushed.
# 3 deneme: her basarisiz denemeden sonra once senkronize ol (non-fast-forward
# ihtimali), sonra artan bekleme ile tekrar dene.
push_ok=0
for attempt in 1 2 3; do
  if git push origin master --quiet; then
    push_ok=1
    break
  fi
  sleep $((attempt * 15))
  github_reachable || continue
  git pull --rebase --autostash --quiet origin master || true
  if [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; then
    git rebase --abort || true
    echo "$(date '+%Y-%m-%d %H:%M:%S') push retry sirasinda cakisma, rebase abort edildi"
    break
  fi
done

if [ "$push_ok" -ne 1 ]; then
  osascript -e 'display notification "Vault backup push FAILED" with title "brainless"'
  exit 1
fi
