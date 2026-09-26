#!/usr/bin/env python3
"""brainless.toml: the one settings file, and the secret store beside it.

The engine has always been configured by BRAINLESS_* environment variables, and
every tool still reads only those. This module is a front door, not a second
system: importing it reads <vault>/brainless.toml and sets each variable the
file names as a DEFAULT. Precedence is therefore environment > file > the tool's
own default, and a vault without the file behaves exactly as before.

    profile = "lite"                     # BRAINLESS_PROFILE

    [owner]
    name = "Ada"                         # BRAINLESS_OWNER_NAME
    lang = "tr"                          # BRAINLESS_OUTPUT_LANG

    [folders]                            # BRAINLESS_FOLDER_<KEY>, see paths.py
    inbox = "Gelen"
    daily = "Notlar"
    sources = ["Arşiv"]                  # BRAINLESS_SOURCES, extra summary sources

    [llm]
    provider = "openai-compatible"       # BRAINLESS_LLM_PROVIDER
    base_url = "http://localhost:11434/v1"
    model = "qwen3:8b"
    fallback = ""                        # BRAINLESS_LLM_FALLBACK
    claude_model = ""                    # BRAINLESS_CLAUDE_MODEL

    [llm.lanes.compile]                  # BRAINLESS_LLM_{PROVIDER,MODEL,FALLBACK}_COMPILE
    provider = "claude-cli"

    [schedule]
    enabled = true                       # BRAINLESS_SCHEDULE

    [commands]                           # BRAINLESS_COMMANDS, see profiles.py
    enable = ["media"]

    [env]                                # escape hatch: any BRAINLESS_* variable
    BRAINLESS_LLM_MAX_CHARS = "12000"

Secrets never go in the file (the vault is a git repo). An api_key found there
is ignored with a warning. Keys live in the OS secret store instead, read at run
time, never written to disk by brainless:
  macOS  : login Keychain, service "brainless", account = the secret's name
  Linux  : libsecret via secret-tool, when installed
  else   : ~/.config/brainless/secrets.env, mode 600
BRAINLESS_SECRETS_BACKEND=keychain|secret-tool|file forces one (tests use file).

tomllib is Python 3.11+. On older Pythons a vault without brainless.toml still
works; a vault with one says so once and runs on environment and defaults.
"""
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import FOLDER_KEYS, vault_root  # noqa: E402

CONFIG_NAME = "brainless.toml"
SERVICE = "brainless"
_SECRET_NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_LANE_KEYS = {"provider": "PROVIDER", "model": "MODEL", "fallback": "FALLBACK"}
_LLM_KEYS = {"provider": "BRAINLESS_LLM_PROVIDER", "base_url": "BRAINLESS_LLM_BASE_URL",
             "model": "BRAINLESS_LLM_MODEL", "fallback": "BRAINLESS_LLM_FALLBACK",
             "claude_model": "BRAINLESS_CLAUDE_MODEL", "max_chars": "BRAINLESS_LLM_MAX_CHARS"}

_loaded = None


def config_path(vault=None):
    return os.path.join(vault or vault_root(), CONFIG_NAME)


def _warn(msg):
    print(f"[config] {msg}", file=sys.stderr)


def load(vault=None):
    """Parsed brainless.toml as a dict; {} when absent or unreadable."""
    path = config_path(vault)
    if not os.path.isfile(path):
        return {}
    try:
        import tomllib
    except ModuleNotFoundError:
        _warn(f"{path} needs Python 3.11+; ignoring it")
        return {}
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as e:
        _warn(f"cannot read {path}: {e}")
        return {}


def _str(v):
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (list, tuple)):
        return ",".join(str(x) for x in v)
    return str(v)


def to_env(data):
    """The {VAR: value} defaults a parsed config implies. Pure; apply() sets them."""
    out = {}
    if "profile" in data:
        out["BRAINLESS_PROFILE"] = data["profile"]
    owner = data.get("owner") or {}
    if "name" in owner:
        out["BRAINLESS_OWNER_NAME"] = owner["name"]
    if "lang" in owner:
        out["BRAINLESS_OUTPUT_LANG"] = owner["lang"]
    folders = data.get("folders") or {}
    for key in FOLDER_KEYS:
        if key in folders:
            out[f"BRAINLESS_FOLDER_{key.upper()}"] = folders[key]
    if "sources" in folders:
        out["BRAINLESS_SOURCES"] = folders["sources"]
    llm = data.get("llm") or {}
    if "api_key" in llm:
        _warn("llm.api_key in brainless.toml is ignored; store it with `brainless secret set llm_api_key`")
    for key, var in _LLM_KEYS.items():
        if key in llm:
            out[var] = llm[key]
    for lane, conf in (llm.get("lanes") or {}).items():
        suffix = re.sub(r"[^A-Z0-9]", "_", lane.upper())
        for key, part in _LANE_KEYS.items():
            if key in (conf or {}):
                out[f"BRAINLESS_LLM_{part}_{suffix}"] = conf[key]
    if "enable" in (data.get("commands") or {}):
        out["BRAINLESS_COMMANDS"] = data["commands"]["enable"]
    if "enabled" in (data.get("schedule") or {}):
        out["BRAINLESS_SCHEDULE"] = data["schedule"]["enabled"]
    for var, val in (data.get("env") or {}).items():
        if var.startswith("BRAINLESS_") and "KEY" not in var and "TOKEN" not in var:
            out[var] = val
        else:
            _warn(f"[env] {var} ignored: only non-secret BRAINLESS_* variables belong here")
    return {k: _str(v) for k, v in out.items()}


def apply(vault=None, force=False):
    """Set the config's variables as environment defaults. Idempotent per process."""
    global _loaded
    if _loaded is not None and not force:
        return _loaded
    _loaded = to_env(load(vault))
    for var, val in _loaded.items():
        os.environ.setdefault(var, val)
    return _loaded


# ─── secrets ──────────────────────────────────────────────────────

def _backend():
    forced = os.environ.get("BRAINLESS_SECRETS_BACKEND")
    if forced:
        return forced
    if sys.platform == "darwin" and shutil.which("security"):
        return "keychain"
    if sys.platform.startswith("linux") and shutil.which("secret-tool"):
        return "secret-tool"
    return "file"


def _secrets_file():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "brainless", "secrets.env")


def _file_read():
    try:
        with open(_secrets_file(), encoding="utf-8") as fh:
            return dict(line.rstrip("\n").split("=", 1) for line in fh if "=" in line)
    except OSError:
        return {}


def _file_write(table):
    path = _secrets_file()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.writelines(f"{k}={v}\n" for k, v in sorted(table.items()))
    os.chmod(path, 0o600)


def _check(name, value=None):
    if not _SECRET_NAME.fullmatch(name):
        raise ValueError(f"bad secret name: {name!r}")
    if value is not None and ("\n" in value or "\r" in value or not value):
        raise ValueError("secret must be a single non-empty line")


def get_secret(name):
    """The stored value or None. Never raises for a missing secret."""
    _check(name)
    backend = _backend()
    if backend == "file":
        return _file_read().get(name)
    if backend == "keychain":
        cmd = ["security", "find-generic-password", "-s", SERVICE, "-a", name, "-w"]
    else:
        cmd = ["secret-tool", "lookup", "service", SERVICE, "account", name]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = r.stdout.rstrip("\n")
    return value if r.returncode == 0 and value else None


def set_secret(name, value):
    """Store a secret. The value never appears on a command line (ps can read those)."""
    _check(name, value)
    backend = _backend()
    if backend == "file":
        table = _file_read()
        table[name] = value
        _file_write(table)
        return backend
    if backend == "keychain":
        # `security -i` reads commands from stdin, which keeps the value off argv.
        quoted = value.replace("\\", "\\\\").replace('"', '\\"')
        cmd, stdin = ["security", "-i"], f'add-generic-password -U -s {SERVICE} -a {name} -w "{quoted}"\n'
    else:
        cmd = ["secret-tool", "store", f"--label=brainless {name}", "service", SERVICE, "account", name]
        stdin = value
    r = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=30)
    if r.returncode != 0 or "error" in (r.stderr or "").lower():
        raise RuntimeError(f"{backend} refused the secret: {(r.stderr or '').strip()[:200]}")
    return backend


def delete_secret(name):
    """Remove a secret; True when something was removed."""
    _check(name)
    backend = _backend()
    if backend == "file":
        table = _file_read()
        if name not in table:
            return False
        del table[name]
        _file_write(table)
        return True
    if backend == "keychain":
        cmd = ["security", "delete-generic-password", "-s", SERVICE, "-a", name]
    else:
        cmd = ["secret-tool", "clear", "service", SERVICE, "account", name]
    return subprocess.run(cmd, capture_output=True, timeout=15).returncode == 0


apply()


if __name__ == "__main__":
    import argparse
    import getpass
    ap = argparse.ArgumentParser(description="Show the settings brainless.toml implies, or manage secrets.")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("show", help="print the variables brainless.toml sets (the default)")
    s = sub.add_parser("secret", help="get, set or delete a secret in the OS store")
    s.add_argument("action", choices=["set", "check", "delete"])
    s.add_argument("name")
    a = ap.parse_args()
    if a.cmd == "secret":
        if a.action == "set":
            val = getpass.getpass(f"{a.name}: ") if sys.stdin.isatty() else sys.stdin.readline().strip()
            print(f"stored in {set_secret(a.name, val)}")
        elif a.action == "check":
            print("present" if get_secret(a.name) else "missing")
            sys.exit(0 if get_secret(a.name) else 1)
        else:
            print("deleted" if delete_secret(a.name) else "not found")
    else:
        path = config_path()
        print(f"# {path}" + ("" if os.path.isfile(path) else " (absent)"))
        for var, val in sorted(_loaded.items()):
            shown = val if os.environ.get(var) == val else f"{val}  (overridden by env: {os.environ.get(var)})"
            print(f"{var}={shown}")
