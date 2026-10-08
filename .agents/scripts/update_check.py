#!/usr/bin/env python3
"""Arch update notification (worker, weekly).

Counts pending packages with `checkupdates` (pacman-contrib, no root needed);
if security-critical ones are among them it reports via Buzz. It does NOT
upgrade automatically: on Arch a partial/automatic upgrade is a breakage risk;
the decision and `pacman -Syu` stay with a human. Goal: prevent silent aging.
"""
import os
import subprocess

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
from owner_profile import WORKER
from i18n import t
from buzz_delivery import send
from functools import partial
send_buzz = partial(send, 'ops')

# Packages that deserve special attention when an update arrives (substring match).
SECURITY_PKGS = (
    "openssl", "openssh", "curl", "wget", "git", "linux", "linux-firmware",
    "ca-certificates", "tailscale", "sudo", "glibc", "systemd", "polkit",
    "python", "nginx", "chromium", "firefox", "gnutls", "libssh", "pam",
)


def main():
    try:
        r = subprocess.run(["checkupdates"], capture_output=True, text=True, timeout=180)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return  # checkupdates missing/hung: exit silently
    # checkupdates: returns 0 when updates exist, 2 when none; stdout lines are "pkg a -> b"
    lines = [line for line in r.stdout.splitlines() if line.strip()]
    if not lines:
        print("no updates")
        return
    names = [line.split()[0] for line in lines]
    security = sorted({n for n in names if any(s in n for s in SECURITY_PKGS)})
    msg = [t("update_check.msg_header", worker=WORKER, count=len(lines))]
    if security:
        msg.append(t("update_check.msg_security", pkgs=", ".join(security[:15])))
    msg.append(t("update_check.msg_hint"))
    send_buzz("\n".join(msg))
    print(f"notification sent ({len(lines)} packages, {len(security)} security)")


if __name__ == "__main__":
    main()
