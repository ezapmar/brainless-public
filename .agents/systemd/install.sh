#!/bin/bash
# Run on the always-on worker: installs the units here into the user systemd.
#   bash .agents/systemd/install.sh
# Every worker unit lives here: the nightly family (nightly, closeout, lint,
# reconcile, resurface, dashboard), thinking, dialectic, classify, research, and
# the capture line (telegram, buzz-capture, tasks, backup, watchdog, spiky, brief,
# reminder, thinkers, radar, content, update), copied from the worker on 2026-09-19.
# The loop below globs *.service and *.timer, so a new unit needs no edit here.
# Template units (buzz-persona@.service) are copied here, but their instances
# are started by .agents/buzz/install_personas.sh.
set -eu
cd "$(dirname "$0")"
mkdir -p ~/.config/systemd/user
for f in *.service *.timer; do
  cp -f "$f" ~/.config/systemd/user/"$f"
done
systemctl --user daemon-reload
for t in *.timer; do
  systemctl --user enable --now "$t"
done
systemctl --user list-timers --all | grep -E "brainless-" || true
