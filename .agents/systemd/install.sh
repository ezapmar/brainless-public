#!/bin/bash
# Run on the always-on worker: installs the units here into the user systemd.
#   bash .agents/systemd/install.sh          shared units only
#   bash .agents/systemd/install.sh --all    also the owner-only units
# Every worker unit lives here: the nightly family (nightly, closeout, lint,
# reconcile, resurface, dashboard), thinking, dialectic, classify, research, and
# the capture line (telegram, buzz-capture, tasks, backup, watchdog, spiky, brief,
# reminder, thinkers, radar, content, update), copied from the worker on 2026-09-19.
# The loop below globs *.service and *.timer, so a new unit needs no edit here.
# brainless-mcp.service and brainless-herdr.service are long-running: copied
# here, started once by hand (systemctl --user enable --now <unit>).
# Template units (buzz-persona@.service) are copied here, but their instances
# are started by .agents/buzz/install_personas.sh.
#
# Vault path: the units in the repo name the vault as %h/projects/brainless so
# git stays quiet. The installed copies are rewritten to the checkout this
# script lives in (the directory holding .agents), so a vault cloned anywhere
# else, e.g. ~/brainless from the root install.sh, runs the right files. Each
# [Service] section also gets Environment=BRAINLESS_VAULT=<that path> unless it
# already sets one. A unit linked in with `systemctl --user link` is the repo
# file itself and is left as it is.
#
# Owner-only units: owner-only.txt names the units that only make sense on the
# owner's own worker (personal jobs, hand-installed compatibility shims). They
# are skipped unless --all is given.
set -eu

ALL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --all) ALL=1; shift ;;
    -h|--help) sed -n 2,25p "$0"; exit 0 ;;
    *) echo "unknown option: $1 (only --all)" >&2; exit 2 ;;
  esac
done

cd "$(dirname "$0")"
VAULT="$(cd ../.. && pwd -P)"
# systemd splits ExecStart on whitespace and expands % specifiers, so a path
# with whitespace cannot be written into a unit; a literal % is doubled.
case "$VAULT" in
  *[[:space:]]*) echo "vault path contains whitespace, systemd units cannot use it: $VAULT" >&2; exit 1 ;;
esac
VAULT_UNIT="${VAULT//%/%%}"

DEST=~/.config/systemd/user
mkdir -p "$DEST"

OWNER_ONLY=" "
if [ "$ALL" -eq 0 ] && [ -f owner-only.txt ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%%#*}"
    line="${line//[[:space:]]/}"
    [ -n "$line" ] && OWNER_ONLY="$OWNER_ONLY$line "
  done < owner-only.txt
fi
is_owner_only() { case "$OWNER_ONLY" in *" $1 "*) return 0 ;; esac; return 1; }

# Copy one unit with the vault path substituted. awk does the literal string
# replacement (no regex or & surprises in the path) and inserts the environment
# line right after each [Service] header whose section does not set it.
render_unit() {  # src dest
  awk -v vault="$VAULT_UNIT" '
    { lines[NR] = $0 }
    END {
      old = "%h/projects/brainless"
      start = 0
      for (i = 1; i <= NR; i++) {
        if (lines[i] ~ /^\[.*\]$/) { start = i; header[i] = lines[i] }
        else if (start && header[start] == "[Service]" && lines[i] ~ /^Environment=.*BRAINLESS_VAULT=/) has[start] = 1
      }
      for (i = 1; i <= NR; i++) {
        line = lines[i]
        out = ""
        while ((p = index(line, old)) > 0) {
          out = out substr(line, 1, p - 1) vault
          line = substr(line, p + length(old))
        }
        print out line
        if (header[i] == "[Service]" && !has[i]) {
          print "Environment=BRAINLESS_VAULT=" vault
          print "Environment=PYTHONPATH=" vault "/tools:" vault "/.agents/scripts"
        }
      }
    }' "$1" > "$2"
}

installed_timers=()
for f in *.service *.timer; do
  if is_owner_only "$f"; then
    echo "skipped (owner-only, use --all): $f"
    continue
  fi
  # A unit linked in with `systemctl --user link` is already this file; cp
  # refuses to copy a file onto itself and set -e would stop the install.
  if [ "$f" -ef "$DEST/$f" ]; then
    case "$f" in *.timer) installed_timers+=("$f") ;; esac
    continue
  fi
  # Write beside the target and rename over it: a plain redirect would write
  # through a symlink left at the target by an older install.
  tmp="$DEST/.$f.tmp"
  render_unit "$f" "$tmp"
  mv -f "$tmp" "$DEST/$f"
  case "$f" in *.timer) installed_timers+=("$f") ;; esac
done
systemctl --user daemon-reload
for t in ${installed_timers[@]+"${installed_timers[@]}"}; do
  systemctl --user enable --now "$t"
done
systemctl --user list-timers --all | grep -E "brainless-" || true
