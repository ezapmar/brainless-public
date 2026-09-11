#!/usr/bin/env bash
# Buzz Layer 1: post today's morning briefing to #daily once ("briefing" identity).
# Runs after every worker pull (worker_backup.sh). Idempotent via a state file.
# Posts only the part before the evening close-out marker; the close-out itself is
# posted by tools/evening_closeout.py when it is written.
set -u
VAULT="${BRAINLESS_VAULT:-$HOME/projects/brainless}"
STATE="$VAULT/.agents/state/buzz_briefing_posted"
TODAY="$(date +%F)"
FILE="$VAULT/Daily Briefings/daily-briefing-$TODAY.md"
# Close-out marker and "no morning briefing" note as written by tools/evening_closeout.py,
# read from the locale (current language plus English) so any vault language works.
MARKERS="$(cd "$VAULT" && python3 -c 'import sys; sys.path.insert(0, "tools"); from i18n import t_list; print("\t".join(t_list("evening_closeout.section_marker")))' 2>/dev/null)"
NO_MORNING="$(cd "$VAULT" && python3 -c 'import sys; sys.path.insert(0, "tools"); from i18n import t_list; print("\n".join(t_list("evening_closeout.missing_briefing_note")))' 2>/dev/null)"
[ -n "$MARKERS" ] || MARKERS="## Evening Close-out"
[ -s "$FILE" ] || exit 0
grep -qx "$TODAY" "$STATE" 2>/dev/null && exit 0
# Skip files that only exist because the close-out opened them (no morning briefing).
[ -n "$NO_MORNING" ] && grep -qF -f <(printf '%s\n' "$NO_MORNING") "$FILE" && exit 0
BODY="$(awk -v m="$MARKERS" 'BEGIN{n=split(m,a,"\t")} {for(i=1;i<=n;i++) if(a[i]!="" && index($0,a[i])==1) exit; print}' "$FILE")"
[ -n "$(printf '%s' "$BODY" | tr -d '[:space:]')" ] || exit 0
if printf '%s\n' "$BODY" | "$VAULT/.agents/scripts/buzz_post.sh" briefing daily 2>&1 | grep -q "posted as"; then
  mkdir -p "$(dirname "$STATE")"; echo "$TODAY" >> "$STATE"
fi
exit 0
