#!/bin/bash
# The day's single commit and push, run last by night.sh.
# Since 2026-11-15 this Mac is the only writer, so there is no pull, no rebase
# and no conflict path: commit what changed, push it to the private remote.
cd "${BRAINLESS_VAULT:-$HOME/projects/brainless}" || exit 1

# GitHub hard-rejects blobs over 100 MB and one oversized file poisons every
# later push. Ignore anything over 95 MB before adding.
find . -type f -size +95M -not -path "./.git/*" -not -path "./_Backup/*" -not -path "./venv/*" -print0 | while IFS= read -r -d '' f; do
  rel="${f#./}"
  if ! git check-ignore -q "$rel"; then
    echo "$rel" >> .gitignore
    echo "$(date '+%F %T') 95MB+ file ignored: $rel"
  fi
done

if [ -n "$(git status --porcelain)" ]; then
  git add -A
  git commit --quiet -m "vault backup: $(date '+%F')" || true
fi

# Private remote guard: `git add -A` holds the whole vault. Push only to a
# GitHub repo that answers 404 to an anonymous request, i.e. a private one.
# It fails closed: the URL checked is the one git pushes to, and a push URL
# that does not parse as github.com/<owner>/<repo> is refused, not skipped.
slug_of() {  # github.com owner/repo from a remote URL, or nothing
  printf '%s' "$1" | sed -nE 's#^(git@github\.com:|ssh://git@github\.com/|https://([^/@]+@)?github\.com/)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/?$#\3#p' | sed 's/\.git$//'
}
push_url=$(git remote get-url --push origin 2>/dev/null || true)
slug=$(slug_of "$push_url")
if printf '%s' "$push_url" | grep -qi 'brainless-public'; then
  echo "$(date '+%F %T') push refused: origin is the public engine repo"
  exit 0
fi
if [ -z "$slug" ]; then
  echo "$(date '+%F %T') push refused: push URL is not a github.com owner/repo the guard can check"
  exit 0
fi
code=000
for try in 1 2 3; do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "https://api.github.com/repos/$slug" || true)
  [ "$code" != "000" ] && break
  sleep $((try * 10))
done
if [ "$code" != "404" ]; then
  echo "$(date '+%F %T') push refused: github.com/$slug is public or unverifiable (HTTP $code)"
  exit 0
fi
[ -f .agents/state/no_push ] && exit 0

for attempt in 1 2 3; do
  git push origin master --quiet && exit 0
  sleep $((attempt * 30))
done
echo "$(date '+%F %T') push failed after 3 attempts; commits stay local until tomorrow"
exit 1
