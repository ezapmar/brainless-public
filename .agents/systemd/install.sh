#!/bin/bash
# Run on the always-on worker: installs the units here into the user systemd.
#   bash .agents/systemd/install.sh
# Nightly family (nightly, closeout, lint, reconcile, resurface, dashboard),
# thinking and dialectic units live here (Phase 0 T4). telegram, buzz-capture,
# tasks, backup, watchdog, spiky, brief, reminder, thinkers, radar, content and
# update timers are still hand-installed on the current worker.
# Template unit'ler (buzz-persona@.service) kopyalanir ama instance'lari
# .agents/buzz/install_personas.sh acar.
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
