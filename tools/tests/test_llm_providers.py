"""The providers added for the lite profile, against a local stub server.

What matters most is not that a request is well formed but where a key goes:
a lane on Grok must never carry the OpenAI key, and a provider with no key must
fail before any request leaves the machine. After that: the Anthropic call
shape, its one retry, a refusal read as a failure, the agent CLIs fed on stdin
from an empty directory, and the research lane refusing to pretend without the
claude CLI.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import llm  # noqa: E402

EM, EN = chr(0x2014), chr(0x2013)


class Stub(BaseHTTPRequestHandler):
    """Records every request; answers from a per-test script of (code, body)."""
    calls: list = []
    script: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Stub.calls.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
        code, payload = Stub.script.pop(0) if Stub.script else (500, {})
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        if code == 529:
            self.send_header("retry-after", "0")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        Stub.calls.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}})
        raw = json.dumps({"data": [{"id": "qwen3:8b"}, {"id": "llama3.2"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


def chat(text):
    return 200, {"choices": [{"message": {"content": text}}]}


def message(text, stop="end_turn"):
    return 200, {"content": [{"type": "text", "text": text}], "stop_reason": stop}


class ProviderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        Stub.calls, Stub.script = [], []
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        state = Path(self.tmp.name)
        keep = {k: v for k, v in os.environ.items()
                if not k.startswith(("BRAINLESS_", "ANTHROPIC_"))}
        keep.update(BRAINLESS_SECRETS_BACKEND="file", XDG_CONFIG_HOME=str(state))
        for patcher in (mock.patch.dict(os.environ, keep, clear=True),
                        mock.patch.object(llm, "STATUS_FILE", str(state / "llm_status")),
                        mock.patch.object(llm, "LOG_FILE", str(state / "llm_log")),
                        mock.patch.object(llm, "_KEYS", {}),
                        mock.patch.object(llm.time, "sleep")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.status = state / "llm_status"

    def env(self, **kv):
        os.environ.update(kv)


class TestPresets(ProviderTest):
    def test_grok_sends_its_own_key_only(self):
        self.env(BRAINLESS_LLM_PROVIDER="grok", BRAINLESS_GROK_BASE_URL=self.url,
                 BRAINLESS_LLM_MODEL="grok-test", BRAINLESS_GROK_API_KEY="xai-1",
                 BRAINLESS_LLM_API_KEY="sk-openai-must-not-travel")
        Stub.script = [chat("hello")]
        self.assertEqual(llm.run_prompt("hi", lane="compile"), "hello")
        call = Stub.calls[0]
        self.assertEqual(call["path"], "/chat/completions")
        self.assertEqual(call["headers"]["authorization"], "Bearer xai-1")
        self.assertEqual(call["body"]["model"], "grok-test")

    def test_missing_key_fails_before_any_request(self):
        self.env(BRAINLESS_LLM_PROVIDER="openai", BRAINLESS_OPENAI_BASE_URL=self.url,
                 BRAINLESS_LLM_MODEL="m")
        self.assertIsNone(llm.run_prompt("hi", lane="compile"))
        self.assertEqual(Stub.calls, [])
        self.assertIn("\tauth\t", self.status.read_text())

    def test_key_from_secret_store(self):
        import config
        config.set_secret("openrouter_api_key", "or-1")
        self.env(BRAINLESS_LLM_PROVIDER="openrouter", BRAINLESS_OPENROUTER_BASE_URL=self.url,
                 BRAINLESS_LLM_MODEL="m")
        Stub.script = [chat("ok")]
        self.assertEqual(llm.run_prompt("hi"), "ok")
        self.assertEqual(Stub.calls[0]["headers"]["authorization"], "Bearer or-1")

    def test_ollama_needs_no_key_and_takes_the_lane_model(self):
        self.env(BRAINLESS_LLM_PROVIDER="ollama", BRAINLESS_OLLAMA_BASE_URL=self.url,
                 BRAINLESS_LLM_MODEL="big", BRAINLESS_LLM_MODEL_NOTE_CLASSIFY="small")
        Stub.script = [chat("a"), chat("b")]
        llm.run_prompt("hi", lane="note-classify")
        llm.run_prompt("hi", lane="compile")
        self.assertNotIn("authorization", Stub.calls[0]["headers"])
        self.assertEqual([c["body"]["model"] for c in Stub.calls], ["small", "big"])

    def test_dashes_cleaned(self):
        self.env(BRAINLESS_LLM_PROVIDER="ollama", BRAINLESS_OLLAMA_BASE_URL=self.url,
                 BRAINLESS_LLM_MODEL="m")
        Stub.script = [chat(f"a {EM} b {EN} c")]
        self.assertEqual(llm.run_prompt("hi"), "a - b - c")


class TestModels(ProviderTest):
    def test_list_models(self):
        self.env(BRAINLESS_OLLAMA_BASE_URL=self.url)
        self.assertEqual(llm.list_models("ollama"), ["llama3.2", "qwen3:8b"])
        self.assertEqual(Stub.calls[0]["path"], "/models")
        self.assertIsNone(llm.list_models("codex-cli"))


class TestLocality(ProviderTest):
    def test_local_detection(self):
        self.assertTrue(llm._is_local("ollama"))
        self.env(BRAINLESS_LLM_BASE_URL="http://localhost:1234/v1")
        self.assertTrue(llm._is_local("openai-compatible"))
        self.env(BRAINLESS_LLM_BASE_URL="https://api.openai.com/v1")
        self.assertFalse(llm._is_local("openai-compatible"))
        self.assertFalse(llm._is_local("anthropic"))

    def test_ollama_lane_never_falls_back_to_cloud(self):
        self.env(BRAINLESS_LLM_FALLBACK="anthropic")
        self.assertIsNone(llm._resolve_fallback("compile", "ollama"))
        self.assertEqual(llm._resolve_fallback("compile", "grok"), "anthropic")

    def test_unknown_provider(self):
        self.env(BRAINLESS_LLM_PROVIDER="gpt-9000")
        self.assertIsNone(llm.run_prompt("hi"))
        self.assertIn("unknown provider", self.status.read_text())


class TestAnthropic(ProviderTest):
    def setUp(self):
        super().setUp()
        self.env(BRAINLESS_LLM_PROVIDER="anthropic", BRAINLESS_ANTHROPIC_BASE_URL=self.url,
                 ANTHROPIC_API_KEY="sk-ant-1")

    def test_call_shape_and_default_model(self):
        Stub.script = [message("hi there")]
        self.assertEqual(llm.run_prompt("hello"), "hi there")
        call = Stub.calls[0]
        self.assertEqual(call["path"], "/v1/messages")
        self.assertEqual(call["headers"]["x-api-key"], "sk-ant-1")
        self.assertEqual(call["headers"]["anthropic-version"], "2023-06-01")
        self.assertEqual(call["body"]["model"], llm.ANTHROPIC_DEFAULT_MODEL)
        self.assertEqual(call["body"]["messages"], [{"role": "user", "content": "hello"}])

    def test_caller_model_is_honoured(self):
        Stub.script = [message("x")]
        llm.run_prompt("hello", model="claude-haiku-4-5")
        self.assertEqual(Stub.calls[0]["body"]["model"], "claude-haiku-4-5")

    def test_one_retry_on_overload(self):
        Stub.script = [(529, {"type": "error"}), message("second time")]
        self.assertEqual(llm.run_prompt("hello"), "second time")
        self.assertEqual(len(Stub.calls), 2)

    def test_auth_is_not_retried(self):
        Stub.script = [(401, {"type": "error"}), message("never")]
        self.assertIsNone(llm.run_prompt("hello"))
        self.assertEqual(len(Stub.calls), 1)
        self.assertIn("\tauth\t", self.status.read_text())

    def test_refusal_is_a_failure(self):
        Stub.script = [message("", stop="refusal")]
        self.assertIsNone(llm.run_prompt("hello"))


class TestAgentCli(ProviderTest):
    def fake(self, name, script):
        bindir = Path(self.tmp.name) / "bin"
        bindir.mkdir(exist_ok=True)
        path = bindir / name
        path.write_text("#!/bin/sh\n" + script)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return mock.patch.object(llm, "resolve", side_effect=lambda n: str(path) if n == name else None)

    def test_codex_reads_stdin_from_an_empty_directory(self):
        # The fake writes the working directory's listing and its stdin to the -o file.
        script = ('while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done\n'
                  'printf "files:%s|" "$(ls -A)" > "$out.tmp"; cat >> "$out.tmp"; mv "$out.tmp" "$out"\n')
        self.env(BRAINLESS_LLM_PROVIDER="codex-cli")
        with self.fake("codex", script):
            out = llm.run_prompt("secret prompt")
        self.assertEqual(out, "files:|secret prompt")

    def test_missing_cli(self):
        self.env(BRAINLESS_LLM_PROVIDER="gemini-cli")
        with mock.patch.object(llm, "resolve", return_value=None):
            self.assertIsNone(llm.run_prompt("hi"))
        self.assertIn("not installed", self.status.read_text())


class TestWebLane(ProviderTest):
    def test_web_without_claude_is_skipped(self):
        with mock.patch.object(llm, "resolve", return_value=None), \
             mock.patch.object(llm, "_dispatch") as dispatch:
            self.assertIsNone(llm.run_prompt("search", web=True, lane="research-salvo"))
        dispatch.assert_not_called()
        self.assertIn("web lane needs the claude CLI", self.status.read_text())


if __name__ == "__main__":
    unittest.main()
