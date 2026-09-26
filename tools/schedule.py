#!/usr/bin/env python3
"""The one scheduler entry of the lite profile: run `jobqueue.py tick` every 15
minutes and at login, and let the queue decide what is due.

    macOS   : ~/Library/LaunchAgents/com.brainless.lite.plist
              (StartInterval 900 + RunAtLoad; a sleeping Mac catches up on wake)
    Linux   : ~/.config/systemd/user/brainless-lite.{service,timer}
              (OnCalendar every 15 min, Persistent=true)
    Windows : Task Scheduler task "brainless-lite", every 15 minutes

    brainless schedule show        print what would be installed, change nothing
    brainless schedule install     write it and load it
    brainless schedule uninstall   unload it and remove it
    brainless schedule status      is it loaded?

Install refuses unless the profile is lite (brainless.toml profile = "lite"),
because the full deployment runs its own timers and a second scheduler would
compile the same vault twice. --force overrides.

The entry carries a short PATH: the folders where the model CLIs (claude, codex,
gemini, ollama, goose) were found at install time, then the usual system folders.
launchd and systemd start jobs with a bare PATH, and copying the installing
shell's PATH would freeze whatever temporary folders it happened to hold.
"""
import argparse
import os
import shutil
import subprocess
import sys
from xml.sax.saxutils import escape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import vault_root  # noqa: E402
import config  # noqa: E402,F401

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LABEL = "com.brainless.lite"
UNIT = "brainless-lite"
INTERVAL_MIN = 15


def platform():
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    return "linux"


def python_bin():
    for rel in (".venv/bin/python", ".venv/Scripts/python.exe"):
        p = os.path.join(ENGINE, rel)
        if os.path.exists(p):
            return p
    return sys.executable


def job_path():
    """PATH for the scheduled job: where the CLIs live now, then the system dirs."""
    from resolve_bin import resolve
    dirs = []
    for name in ("claude", "codex", "gemini", "ollama", "goose", "git"):
        found = resolve(name)
        if found:
            dirs.append(os.path.dirname(found))
    dirs += [os.path.dirname(python_bin()), os.path.expanduser("~/.local/bin"),
             "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    return os.pathsep.join(dict.fromkeys(d for d in dirs if d))


def command():
    return [python_bin(), os.path.join(ENGINE, "tools", "jobqueue.py"), "tick"]


def log_path():
    d = os.path.join(vault_root(), "logs")
    return os.path.join(d, "lite-tick.log")


def launchd_plist(env_path=None):
    env = {"BRAINLESS_VAULT": vault_root(), "PATH": env_path or job_path()}
    args = "".join(f"\n    <string>{escape(a)}</string>" for a in command())
    envs = "".join(f"\n    <key>{escape(k)}</key><string>{escape(v)}</string>" for k, v in env.items())
    log = escape(log_path())
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>{args}
  </array>
  <key>EnvironmentVariables</key>
  <dict>{envs}
  </dict>
  <key>WorkingDirectory</key><string>{escape(ENGINE)}</string>
  <key>StartInterval</key><integer>{INTERVAL_MIN * 60}</integer>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>Nice</key><integer>5</integer>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict>
</plist>
"""


def _sd_quote(a):
    return '"' + a.replace("\\", "\\\\").replace('"', '\\"') + '"'


def systemd_units(env_path=None):
    exec_start = " ".join(_sd_quote(a) for a in command())
    service = f"""[Unit]
Description=brainless lite: run what is due in the job queue

[Service]
Type=oneshot
WorkingDirectory={ENGINE}
Environment={_sd_quote("BRAINLESS_VAULT=" + vault_root())}
Environment={_sd_quote("PATH=" + (env_path or job_path()))}
ExecStart={exec_start}
Nice=5
"""
    timer = f"""[Unit]
Description=brainless lite tick every {INTERVAL_MIN} minutes

[Timer]
OnBootSec=2min
OnCalendar=*:0/{INTERVAL_MIN}
Persistent=true

[Install]
WantedBy=timers.target
"""
    return {f"{UNIT}.service": service, f"{UNIT}.timer": timer}


def schtasks_command():
    tr = " ".join(f'"{a}"' for a in command())
    return ["schtasks", "/Create", "/F", "/SC", "MINUTE", "/MO", str(INTERVAL_MIN),
            "/TN", UNIT, "/TR", tr]


def targets():
    """{path: content} this platform would write (empty on Windows)."""
    kind = platform()
    if kind == "macos":
        return {os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist"): launchd_plist()}
    if kind == "linux":
        base = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                            "systemd", "user")
        return {os.path.join(base, name): body for name, body in systemd_units().items()}
    return {}


def _run(cmd, check=False):
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def install(force=False):
    if os.environ.get("BRAINLESS_PROFILE", "").strip() != "lite" and not force:
        print("refused: this vault is not on the lite profile (brainless.toml profile = \"lite\"); "
              "the full deployment has its own timers. --force to install anyway.", file=sys.stderr)
        return 2
    os.makedirs(os.path.dirname(log_path()), exist_ok=True)
    kind = platform()
    if kind == "windows":
        r = _run(schtasks_command())
        print(r.stdout.strip() or r.stderr.strip())
        return r.returncode
    for path, body in targets().items():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        print(f"wrote {path}")
    if kind == "macos":
        path = next(iter(targets()))
        domain = f"gui/{os.getuid()}"
        _run(["launchctl", "bootout", domain, path])      # absent the first time; fine
        r = _run(["launchctl", "bootstrap", domain, path])
        if r.returncode != 0:
            print(f"launchctl bootstrap failed: {r.stderr.strip()}", file=sys.stderr)
            return r.returncode
    else:
        for cmd in (["systemctl", "--user", "daemon-reload"],
                    ["systemctl", "--user", "enable", "--now", f"{UNIT}.timer"]):
            r = _run(cmd)
            if r.returncode != 0:
                print(f"{' '.join(cmd)} failed: {r.stderr.strip()}", file=sys.stderr)
                return r.returncode
        if shutil.which("loginctl"):
            print("tip: `loginctl enable-linger` keeps the timer running while you are logged out")
    print(f"installed: brainless tick every {INTERVAL_MIN} minutes; log {log_path()}")
    return 0


def uninstall():
    kind = platform()
    if kind == "windows":
        return _run(["schtasks", "/Delete", "/F", "/TN", UNIT]).returncode
    if kind == "macos":
        path = next(iter(targets()))
        _run(["launchctl", "bootout", f"gui/{os.getuid()}", path])
    else:
        _run(["systemctl", "--user", "disable", "--now", f"{UNIT}.timer"])
    for path in targets():
        if os.path.exists(path):
            os.remove(path)
            print(f"removed {path}")
    if kind == "linux":
        _run(["systemctl", "--user", "daemon-reload"])
    return 0


def status():
    kind = platform()
    if kind == "macos":
        r = _run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"])
    elif kind == "linux":
        r = _run(["systemctl", "--user", "is-active", f"{UNIT}.timer"])
    else:
        r = _run(["schtasks", "/Query", "/TN", UNIT])
    print("loaded" if r.returncode == 0 else "not installed")
    return 0 if r.returncode == 0 else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="brainless schedule", description=__doc__.splitlines()[0])
    ap.add_argument("action", nargs="?", default="show", choices=["show", "install", "uninstall", "status"])
    ap.add_argument("--force", action="store_true")
    ns = ap.parse_args(argv)
    if ns.action == "install":
        return install(ns.force)
    if ns.action == "uninstall":
        return uninstall()
    if ns.action == "status":
        return status()
    if platform() == "windows":
        print(" ".join(schtasks_command()))
    for path, body in targets().items():
        print(f"# {path}\n{body}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
