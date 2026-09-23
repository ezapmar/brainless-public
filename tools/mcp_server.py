#!/usr/bin/env python3
"""mcp_server.py: the wiki, read-only, for any MCP client.

An agent outside this repo (a Buzz agent, Claude Desktop, a Claude Code session
in another project) should ask the vault the same way an agent inside it does:
through tools/wiki_search.py and the compiled pages, not by grepping raw notes.
This server exposes exactly that and nothing else. It cannot write.

Tools:
  search(query, k)   ranked pages, the same search() the retrieval eval scores
  read_page(path)    one .wiki page as markdown
  index(topic)       INDEX.md, or one topic index from .wiki/_index/

What may leave: a page git would not push does not go out through this server
either. Every path is checked against .gitignore (finance resources, family folders, the
other local-only rules) and against a second, hard-coded deny list, so a
gitignore edit alone cannot open them. Only .md under .wiki/ is served.

Transports:
  stdio (default)   for a client that starts the server itself
  --http HOST:PORT  Streamable HTTP, JSON responses, for a client on another
                    machine. Bind it to the Tailscale address, never 0.0.0.0.
                    Every request needs "Authorization: Bearer <token>"; the
                    token lives in ~/.config/brainless/mcp_token (mode 600,
                    created on first run).

Standard library only, like the rest of the core.

Usage:
  python3 tools/mcp_server.py
  python3 tools/mcp_server.py --http 100.x.y.z:8765
"""
import argparse
import json
import os
import re
import secrets
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wiki_search import VAULT, mode, passage, search  # noqa: E402

WIKI = VAULT / ".wiki"
NAME, VERSION = "brainless-wiki", (VAULT / "VERSION").read_text().strip() if (VAULT / "VERSION").exists() else "0"
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
TOKEN_FILE = Path.home() / ".config" / "brainless" / "mcp_token"
MAX_PAGE = 60_000  # characters; a raw import can be a whole book

# Second lock behind .gitignore: these never leave, whatever the ignore file says.
DENY = re.compile(r"finance_resources|security-incidents|official-docs|/_archive/", re.I)

TOOLS = [
    {"name": "search",
     "description": "Search the brainless wiki (compiled summaries, concepts, entities, projects, "
                    "filed decisions). Returns ranked pages with the matching passage, the line it is on "
                    "and an English summary. Ask in Turkish or English. Cite as path:line; "
                    "read the whole page with read_page.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "What you want to find, in plain words"},
         "k": {"type": "integer", "minimum": 1, "maximum": 25, "default": 8}},
         "required": ["query"]},
     "annotations": {"readOnlyHint": True}},
    {"name": "read_page",
     "description": "Read one wiki page as markdown, by the path search returned (e.g. .wiki/concepts/x.md).",
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string"}}, "required": ["path"]},
     "annotations": {"readOnlyHint": True}},
    {"name": "index",
     "description": "The wiki's table of contents: INDEX.md with no topic, or one topic index "
                    "(e.g. 'summaries-library-books'). With topic='list', the available topics.",
     "inputSchema": {"type": "object", "properties": {"topic": {"type": "string"}}},
     "annotations": {"readOnlyHint": True}},
]


class Refused(Exception):
    pass


def ignored(rels: list[str]) -> set[str]:
    """The subset git would not push. Fails closed: if git cannot answer, all."""
    if not rels:
        return set()
    try:
        out = subprocess.run(["git", "-C", str(VAULT), "check-ignore", "--no-index", "--stdin", "-z"],
                             input="\0".join(rels) + "\0", capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return set(rels)
    if out.returncode not in (0, 1):
        return set(rels)
    return {p for p in out.stdout.split("\0") if p}


def allowed(rels: list[str]) -> list[str]:
    ok = [r for r in rels if r.startswith(".wiki/") and r.endswith(".md") and not DENY.search("/" + r)]
    blocked = ignored(ok)
    return [r for r in ok if r not in blocked]


def resolve(path: str) -> Path:
    p = (VAULT / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    try:
        rel = p.relative_to(VAULT.resolve()).as_posix()
    except ValueError:
        raise Refused("only pages under .wiki/ are served")
    if not allowed([rel]) or not p.is_file():
        raise Refused(f"not available: {path}")
    return p


def _summary(text: str) -> str:
    m = re.search(r"^summary_en:\s*(.+)$", text[:4000], re.M)
    return m.group(1).strip().strip('"') if m else ""


def tool_search(query: str, k: int = 8) -> str:
    k = max(1, min(int(k), 25))
    ranked = search(query, k=k * 2)
    rels = [os.path.relpath(d["path"], VAULT) for _, d in ranked]
    ok = set(allowed(rels))
    out = []
    for (score, d), rel in zip(ranked, rels):
        if rel in ok:
            src = passage(d, query)
            out.append({"path": rel, "line": src["line"], "at": src["at"], "score": round(score, 4),
                        "summary_en": _summary(d["text"]), "passage": src["passage"]})
        if len(out) >= k:
            break
    return json.dumps({"mode": mode(), "results": out}, ensure_ascii=False, indent=1)


def tool_read_page(path: str) -> str:
    text = resolve(path).read_text(errors="ignore")
    if len(text) > MAX_PAGE:
        text = text[:MAX_PAGE] + f"\n\n[truncated at {MAX_PAGE} characters]"
    return text


def tool_index(topic: str | None = None) -> str:
    if topic == "list":
        return "\n".join(sorted(p.stem.removeprefix("index-") for p in (WIKI / "_index").glob("index-*.md")))
    rel = ".wiki/INDEX.md" if not topic else f".wiki/_index/index-{topic.removeprefix('index-')}.md"
    # Index lines name pages; a line naming a page this server would refuse goes too.
    return "\n".join(l for l in tool_read_page(rel).splitlines() if not DENY.search(l))


def call(name: str, args: dict) -> str:
    if name == "search":
        return tool_search(args.get("query", ""), args.get("k", 8))
    if name == "read_page":
        return tool_read_page(args.get("path", ""))
    if name == "index":
        return tool_index(args.get("topic"))
    raise Refused(f"unknown tool: {name}")


def handle(msg: dict) -> dict | None:
    """One JSON-RPC message in, its response out (None for a notification)."""
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    if method == "initialize":
        want = params.get("protocolVersion")
        return ok({"protocolVersion": want if want in PROTOCOLS else PROTOCOLS[0],
                   "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": {"name": NAME, "version": VERSION},
                   "instructions": "Read-only access to the owner's compiled wiki. Search first, "
                                   "then read_page. Cite pages by path. Do not quote finance "
                                   "figures tied to a named person or customer."})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        try:
            text, err = call(params.get("name", ""), params.get("arguments") or {}), False
        except Refused as e:
            text, err = str(e), True
        except Exception as e:  # a tool failure is a result the model can read, not a crash
            text, err = f"error: {e}", True
        return ok({"content": [{"type": "text", "text": text}], "isError": err})
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}


def serve_stdio():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            resp = handle(msg) if isinstance(msg, dict) else None
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


def token() -> str:
    try:
        return TOKEN_FILE.read_text().strip()
    except OSError:
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        t = secrets.token_urlsafe(32)
        fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(t + "\n")
        return t


def make_handler(secret: str):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body=None):
            data = json.dumps(body, ensure_ascii=False).encode() if body is not None else b""
            self.send_response(code)
            if body is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorised(self) -> bool:
            got = self.headers.get("Authorization", "")
            return secrets.compare_digest(got.encode(), f"Bearer {secret}".encode())

        def do_POST(self):
            if self.path.rstrip("/") != "/mcp":
                return self._send(404)
            # DNS rebinding guard from the spec: a browser page must not reach us.
            if self.headers.get("Origin"):
                return self._send(403)
            if not self._authorised():
                return self._send(401)
            try:
                msg = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            except ValueError:
                return self._send(400, {"jsonrpc": "2.0", "id": None,
                                        "error": {"code": -32700, "message": "parse error"}})
            if isinstance(msg, list):
                out = [r for r in (handle(m) for m in msg if isinstance(m, dict)) if r]
                return self._send(200, out) if out else self._send(202)
            resp = handle(msg) if isinstance(msg, dict) else None
            return self._send(200, resp) if resp else self._send(202)

        def do_GET(self):
            self._send(405)  # no server-initiated stream

        def log_message(self, fmt, *a):  # one line per call to stderr, no bodies
            sys.stderr.write(f"{self.address_string()} {fmt % a}\n")
    return Handler


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", metavar="HOST:PORT")
    args = ap.parse_args(argv)
    if not args.http:
        serve_stdio()
        return 0
    host, _, port = args.http.rpartition(":")
    if host in ("", "0.0.0.0", "::"):
        print("bind to one address (the Tailscale IP), not all interfaces", file=sys.stderr)
        return 2
    srv = ThreadingHTTPServer((host, int(port)), make_handler(token()))
    print(f"brainless MCP on http://{host}:{port}/mcp (token in {TOKEN_FILE})", file=sys.stderr)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
