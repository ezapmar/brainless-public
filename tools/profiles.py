#!/usr/bin/env python3
"""Which `brainless` commands a lite vault shows.

The full deployment has commands that assume its other machines and services:
the evening close-out and the Today queue post to Buzz, backup expects a git
remote and an age key, health reads the worker. On a lite vault they would
only fail in confusing ways, so the dispatcher hides them. Hidden is not gone:
`brainless profile enable media` turns one on, and it is recorded in
brainless.toml as

    [commands]
    enable = ["media"]

which config.py passes on as BRAINLESS_COMMANDS. The full profile (no
brainless.toml, or profile = "full") shows everything.

    brainless profile                 show the profile and what is hidden
    brainless profile enable <cmd>
    brainless profile disable <cmd>
"""
import os
import re
import sys

from paths import vault_root
import config

LITE = {"init", "doctor", "add", "tick", "queue", "schedule", "concepts", "config", "profile",
        "compile", "digest", "search", "file", "lint", "calibrate", "dialectic",
        "update", "vault", "version", "help", "-h", "--help", "--version"}
FULL_ONLY = {"closeout", "dashboard", "today", "media", "backup", "graph", "eval", "chats",
             "health", "export"}


def is_lite():
    return os.environ.get("BRAINLESS_PROFILE", "").strip() == "lite"


def enabled():
    return {c.strip() for c in (os.environ.get("BRAINLESS_COMMANDS") or "").split(",") if c.strip()}


def allowed(cmd):
    return not is_lite() or cmd in LITE or cmd in enabled() or cmd not in FULL_ONLY


def set_enabled(cmds, vault=None):
    """Rewrite the [commands] enable line of brainless.toml, adding the section if needed."""
    path = config.config_path(vault)
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    line = "enable = [" + ", ".join(f'"{c}"' for c in sorted(cmds)) + "]"
    if re.search(r"^\[commands\]\s*$", text, re.M):
        section = re.search(r"^\[commands\]\s*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
        body = section.group(1)
        new_body = (re.sub(r"^enable\s*=.*$", line, body, count=1, flags=re.M)
                    if re.search(r"^enable\s*=", body, re.M) else "\n" + line + body)
        text = text[:section.start(1)] + new_body + text[section.end(1):]
    else:
        text = text.rstrip("\n") + f"\n\n[commands]\n{line}\n"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["check"] and len(args) == 2:
        if allowed(args[1]):
            return 0
        print(f"brainless: `{args[1]}` belongs to the full deployment and is hidden on the lite "
              f"profile. `brainless profile enable {args[1]}` turns it on.", file=sys.stderr)
        return 3
    if args[:1] in (["enable"], ["disable"]) and len(args) == 2:
        cmd = args[1]
        if cmd not in FULL_ONLY:
            print(f"{cmd} is not a hidden command; hidden ones: {', '.join(sorted(FULL_ONLY))}")
            return 2
        if not os.path.exists(config.config_path()):
            print("no brainless.toml here, so nothing is hidden (full profile)")
            return 2
        cmds = enabled() | {cmd} if args[0] == "enable" else enabled() - {cmd}
        set_enabled(cmds)
        print(f"{cmd} {'enabled' if args[0] == 'enable' else 'hidden again'}")
        return 0
    print(f"profile: {'lite' if is_lite() else 'full'} ({vault_root()})")
    if is_lite():
        hidden = sorted(FULL_ONLY - enabled())
        print("hidden: " + (", ".join(hidden) or "nothing"))
        if enabled():
            print("enabled: " + ", ".join(sorted(enabled())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
