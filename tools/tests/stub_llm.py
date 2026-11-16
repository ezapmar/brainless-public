#!/usr/bin/env python3
"""A stand-in OpenAI-compatible model for install tests: every chat answer is
"OK", and /models lists one model. Used by the lite-install CI workflow so the
installer's test call, doctor and a tick run on a clean machine with no key.

    python3 tools/tests/stub_llm.py [port]      (default 8765, 127.0.0.1 only)
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def _send(self, payload):
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self._send({"data": [{"id": "stub"}]})

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self._send({"choices": [{"message": {"content": "OK"}}]})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
