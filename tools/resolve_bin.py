"""Resolve external CLI binaries without hardcoding version-pinned paths.

Pinning a path like ~/.nvm/versions/node/v22.19.0/bin/claude breaks the moment
node is upgraded. These resolvers prefer PATH, then fall back to discovering the
binary across installed versions.
"""
import glob
import os
import re
import shutil


def _node_version_key(path: str) -> tuple:
    m = re.search(r"/v(\d+)\.(\d+)\.(\d+)/", path)
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


def resolve_claude() -> str:
    """Locate the claude CLI: PATH first, then the newest nvm node install."""
    found = shutil.which("claude")
    if found:
        return found
    candidates = sorted(
        glob.glob(os.path.expanduser("~/.nvm/versions/node/*/bin/claude")),
        key=_node_version_key,
        reverse=True,
    )
    for c in candidates:
        if os.access(c, os.X_OK):
            return c
    # Last resort: trust that PATH will be populated at call time.
    return "claude"


# Where installers put CLIs when they are not on a scheduler's minimal PATH
# (launchd and systemd user units start with little more than /usr/bin).
_EXTRA_DIRS = ("~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin",
               "~/.npm-global/bin", "~/.bun/bin", "~/.cargo/bin")


def resolve(name: str) -> str | None:
    """Locate any CLI (gemini, codex, ollama, ...): PATH, the usual install
    directories, then the newest nvm node install. None when not installed."""
    if name == "claude":
        found = resolve_claude()
        return found if os.path.isabs(found) else shutil.which(found)
    found = shutil.which(name)
    if found:
        return found
    for d in _EXTRA_DIRS:
        p = os.path.join(os.path.expanduser(d), name)
        if os.access(p, os.X_OK):
            return p
    for p in sorted(glob.glob(os.path.expanduser(f"~/.nvm/versions/node/*/bin/{name}")),
                    key=_node_version_key, reverse=True):
        if os.access(p, os.X_OK):
            return p
    return None
