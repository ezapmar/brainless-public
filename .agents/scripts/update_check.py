#!/usr/bin/env python3
"""Arch guncelleme bildirimi (worker, haftalik).

`checkupdates` (pacman-contrib, root gerektirmez) ile bekleyen paketleri
sayar; guvenlik-kritik olanlar varsa Telegram'dan haber verir. Otomatik
UPGRADE YAPMAZ: Arch'ta kismi/otomatik upgrade kirilma riskidir; karar ve
`pacman -Syu` insanda kalir. Amac: sessizce eskimeyi engellemek.
"""
import os
import subprocess
import sys

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
sys.path.insert(0, os.path.join(VAULT, ".agents", "scripts"))
sys.path.insert(0, os.path.join(VAULT, "tools"))
from owner_profile import WORKER  # noqa: E402
from watchdog import send_telegram

# Guncellemesi geldiginde bilhassa dikkat edilecek paketler (ic string eslesme).
SECURITY_PKGS = (
    "openssl", "openssh", "curl", "wget", "git", "linux", "linux-firmware",
    "ca-certificates", "tailscale", "sudo", "glibc", "systemd", "polkit",
    "python", "nginx", "chromium", "firefox", "gnutls", "libssh", "pam",
)


def main():
    try:
        r = subprocess.run(["checkupdates"], capture_output=True, text=True, timeout=180)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return  # checkupdates yok/takildi: sessiz cik
    # checkupdates: guncelleme varsa 0, yoksa 2 doner; stdout satirlari "pkg a -> b"
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    if not lines:
        print("guncelleme yok")
        return
    names = [l.split()[0] for l in lines]
    security = sorted({n for n in names if any(s in n for s in SECURITY_PKGS)})
    msg = [f"📦 {WORKER}: {len(lines)} paket guncellenebilir."]
    if security:
        msg.append("Guvenlik-ilgili: " + ", ".join(security[:15]))
    msg.append("Hazir oldugunda: sudo pacman -Syu")
    send_telegram("\n".join(msg))
    print(f"bildirim gonderildi ({len(lines)} paket, {len(security)} guvenlik)")


if __name__ == "__main__":
    main()
