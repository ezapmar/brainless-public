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
