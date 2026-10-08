#!/usr/bin/env python3
"""The one scheduler entry of the lite profile: run `jobqueue.py tick` every 15
minutes and at login, and let the queue decide what is due.

    macOS   : ~/Library/LaunchAgents/com.brainless.lite.plist
              (StartInterval 900 + RunAtLoad; a sleeping Mac catches up on wake)
    Linux   : ~/.config/systemd/user/brainless-lite.{service,timer}
              (OnCalendar every 15 min, Persistent=true); without systemd
              (WSL, containers, some distros) one crontab line instead
    Windows : Task Scheduler task "brainless-lite" (at logon + every 15 min,
              StartWhenAvailable, runs on battery), pythonw so no console flashes

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

from paths import vault_root, python_path
import config  # noqa: F401

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LABEL = "com.brainless.lite"
UNIT = "brainless-lite"
INTERVAL_MIN = 15


def platform():
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    if not shutil.which("systemctl") or not os.path.isdir("/run/systemd/system"):
        return "cron"
    return "linux"


CRON_MARK = "# brainless-lite"


def cron_line():
    cmd = " ".join(_sh_quote(a) for a in command())
    return (f"*/{INTERVAL_MIN} * * * * cd {_sh_quote(ENGINE)} && BRAINLESS_VAULT={_sh_quote(vault_root())} "
            f"{cmd} >> {_sh_quote(log_path())} 2>&1 {CRON_MARK}")


def _sh_quote(a):
    return "'" + a.replace("'", "'\\''") + "'"


def _crontab():
    r = _run(["crontab", "-l"])
    return r.stdout if r.returncode == 0 else ""


def _set_crontab(lines):
    body = "".join(line + "\n" for line in lines)
    return subprocess.run(["crontab", "-"], input=body, capture_output=True, text=True)


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


def windows_command():
    """pythonw: no console window every 15 minutes. -X utf8: notes are UTF-8."""
    py = python_bin()
    w = os.path.join(os.path.dirname(py), "pythonw.exe")
    return [w if os.path.exists(w) else py, "-X", "utf8", os.path.join(ENGINE, "tools", "jobqueue.py"), "tick"]


def log_path():
    d = os.path.join(vault_root(), "logs")
    return os.path.join(d, "lite-tick.log")


def launchd_plist(env_path=None):
    env = {"BRAINLESS_VAULT": vault_root(), "PYTHONPATH": python_path(), "PATH": env_path or job_path()}
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
Environment={_sd_quote("PYTHONPATH=" + python_path())}
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


def _win_user():
    user = os.environ.get("USERNAME", "")
    domain = os.environ.get("USERDOMAIN", "")
    return f"{domain}\\{user}" if domain and user else user


def task_xml(logon=True):
    """Task Scheduler definition. XML rather than /SC MINUTE because only XML
    carries catch-up (StartWhenAvailable), battery and working-directory settings,
    and it has no 261-character limit on the command."""
    exe, *args = windows_command()
    quoted = " ".join(f'"{a}"' if " " in a else a for a in args)
    user = escape(_win_user())
    logon_trigger = (f"""
    <LogonTrigger><Enabled>true</Enabled><UserId>{user}</UserId></LogonTrigger>""" if logon else "")
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>brainless lite: run what is due in the job queue</Description></RegistrationInfo>
  <Triggers>{logon_trigger}
    <TimeTrigger>
      <StartBoundary>2026-01-01T00:00:00</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition><Interval>PT{INTERVAL_MIN}M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author"><UserId>{user}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT3H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(exe)}</Command>
      <Arguments>{escape(quoted)}</Arguments>
      <WorkingDirectory>{escape(ENGINE)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _task_file():
    return os.path.join(ENGINE, ".agents", "state", f"{UNIT}.xml")


def install_windows():
    path = _task_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    r = None
    for logon in (True, False):     # some machines refuse a logon trigger without admin
        with open(path, "w", encoding="utf-16") as fh:
            fh.write(task_xml(logon))
        r = _run(["schtasks", "/Create", "/F", "/TN", UNIT, "/XML", path])
        if r.returncode == 0:
            if not logon:
                print("note: registered without the at-logon trigger; the 15 minute one catches up")
            break
    print(r.stdout.strip() or r.stderr.strip())
    return r.returncode


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
        rc = install_windows()
        if rc == 0:
            print(f"installed: brainless tick every {INTERVAL_MIN} minutes; log {log_path()}")
        return rc
    if kind == "cron":
        if not shutil.which("crontab"):
            print("no systemd and no crontab here; run `brainless tick` yourself, or add this "
                  f"to any scheduler:\n  {cron_line()}", file=sys.stderr)
            return 1
        keep = [line for line in _crontab().splitlines() if not line.endswith(CRON_MARK)]
        r = _set_crontab(keep + [cron_line()])
        if r.returncode != 0:
            print(f"crontab refused the entry: {r.stderr.strip()}", file=sys.stderr)
            return r.returncode
        print(f"installed: crontab line, brainless tick every {INTERVAL_MIN} minutes; log {log_path()}")
        print("note: cron does not catch up after sleep; the next tick runs whatever was missed")
        return 0
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
        if os.path.exists(_task_file()):
            os.remove(_task_file())
        return _run(["schtasks", "/Delete", "/F", "/TN", UNIT]).returncode
    if kind == "cron":
        if shutil.which("crontab"):
            lines = _crontab().splitlines()
            keep = [line for line in lines if not line.endswith(CRON_MARK)]
            if keep != lines:
                _set_crontab(keep)
                print("removed the crontab line")
        return 0
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


def loaded():
    """True when this platform's entry is installed and active."""
    kind = platform()
    try:
        if kind == "cron":
            return shutil.which("crontab") is not None and any(
                line.endswith(CRON_MARK) for line in _crontab().splitlines())
        if kind == "macos":
            r = _run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"])
        elif kind == "linux":
            r = _run(["systemctl", "--user", "is-active", f"{UNIT}.timer"])
        else:
            r = _run(["schtasks", "/Query", "/TN", UNIT])
    except OSError:
        return False
    return r.returncode == 0


def status():
    ok = loaded()
    print("loaded" if ok else "not installed")
    return 0 if ok else 1


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
    if platform() == "cron":
        print(f"# crontab line\n{cron_line()}")
    if platform() == "windows":
        print(f"# schtasks /Create /TN {UNIT} /XML {_task_file()}\n{task_xml()}")
    for path, body in targets().items():
        print(f"# {path}\n{body}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
