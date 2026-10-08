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
import tempfile
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
import llm

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


def message(text, stop="end_turn", usage=None):
    body = {"content": [{"type": "text", "text": text}], "stop_reason": stop}
    if usage:
        body["usage"] = usage
    return 200, body


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
        keep.update(BRAINLESS_SECRETS_BACKEND="file", XDG_CONFIG_HOME=str(state),
                    BRAINLESS_SYSTEM_PROMPT_FILE=str(state / "no-rules.md"))
        for patcher in (mock.patch.dict(os.environ, keep, clear=True),
                        mock.patch.object(llm, "STATUS_FILE", str(state / "llm_status")),
                        mock.patch.object(llm, "LOG_FILE", str(state / "llm_log")),
                        mock.patch.object(llm, "COST_FILE", str(state / "llm_costs.jsonl")),
                        mock.patch.object(llm, "BUDGET_FILE", str(state / "llm_budget.json")),
                        mock.patch.object(llm, "_KEYS", {}),
                        mock.patch.object(llm, "_SYSTEM_PROMPT", {}),
                        mock.patch.object(llm.time, "sleep")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.status = state / "llm_status"
        self.costs = state / "llm_costs.jsonl"
        self.state = state

    def ledger(self):
        if not self.costs.exists():
            return []
        return [json.loads(line) for line in self.costs.read_text().splitlines() if line.strip()]

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

    def test_usage_is_priced_and_ledgered(self):
        usage = {"input_tokens": 1000, "output_tokens": 200,
                 "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        Stub.script = [message("x", usage=usage)]
        llm.run_prompt("hello", lane="compile", model="claude-opus-4-8")
        rows = self.ledger()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["lane"], row["provider"], row["billed"]), ("compile", "anthropic", "api"))
        # 1000 in at $5/M + 200 out at $25/M
        self.assertAlmostEqual(row["usd"], 0.005 + 0.005, places=6)
        self.assertEqual((row["in"], row["out"]), (1000, 200))

    def test_price_override_and_unknown_model(self):
        self.env(BRAINLESS_PRICE_CLAUDE_OPUS_4_8="1,1,1,1")
        self.assertAlmostEqual(llm.cost_usd("claude-opus-4-8", {"input_tokens": 1_000_000}), 1.0)
        self.assertIsNone(llm.cost_usd("some-other-model", {"input_tokens": 5}))
        # a dated id still finds its family
        self.assertIsNotNone(llm.cost_usd("claude-sonnet-5-5-20260901", {"input_tokens": 5}))

    def test_billing_error_is_classified_and_handed_to_the_fallback(self):
        self.env(BRAINLESS_LLM_FALLBACK="claude-cli")
        Stub.script = [(400, {"type": "error", "error": {"type": "invalid_request_error",
                                                         "message": "Your credit balance is too low to access the Anthropic API."}})]
        with mock.patch.object(llm, "_run_claude_cli", return_value="from the plan") as cli:
            self.assertEqual(llm.run_prompt("hello", lane="compile"), "from the plan")
        cli.assert_called_once()
        self.assertEqual(len(Stub.calls), 1)  # a billing error is not retried
        log = (self.state / "llm_log").read_text()
        self.assertIn("\tbilling\t", log)
        self.assertIn("falling back to claude-cli", log)

    def test_402_is_billing(self):
        Stub.script = [(402, {"type": "error"})]
        self.assertIsNone(llm.run_prompt("hello"))
        self.assertIn("\tbilling\t", self.status.read_text())

    def test_system_prompt_file_is_sent_when_present(self):
        rules = self.state / "rules.md"
        rules.write_text("No dashes. Turkish for notes.\n")
        self.env(BRAINLESS_SYSTEM_PROMPT_FILE=str(rules))
        Stub.script = [message("x")]
        llm.run_prompt("hello")
        system = Stub.calls[0]["body"]["system"]
        self.assertTrue(system.startswith("No dashes. Turkish for notes."))
        self.assertIn("Owner's language: Write in", system)

    def test_workspace_header_only_when_configured(self):
        Stub.script = [message("x")]
        llm.run_prompt("hello")
        self.assertNotIn("anthropic-workspace-id", Stub.calls[0]["headers"])
        self.env(BRAINLESS_ANTHROPIC_WORKSPACE_ID="wrkspc_123")
        Stub.script = [message("x")]
        llm.run_prompt("hello")
        self.assertEqual(Stub.calls[1]["headers"]["anthropic-workspace-id"], "wrkspc_123")

    def test_no_system_prompt_without_the_file(self):
        Stub.script = [message("x")]
        llm.run_prompt("hello")
        self.assertNotIn("system", Stub.calls[0]["body"])


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


class TestClaudeCli(ProviderTest):
    """The claude CLI gets the prompt on stdin, and a missing binary is a recorded
    failure, not an exception that skips the fallback lane (2026-10-08)."""

    def fake_claude(self, script):
        bindir = Path(self.tmp.name) / "bin"
        bindir.mkdir(exist_ok=True)
        path = bindir / "claude"
        path.write_text("#!/bin/sh\n" + script)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return mock.patch.object(llm, "resolve_claude", return_value=str(path))

    def test_prompt_arrives_on_stdin_not_argv(self):
        self.env(BRAINLESS_LLM_PROVIDER="claude-cli")
        with self.fake_claude('printf "argv:%s|stdin:" "$*"; cat\n'):
            out = llm.run_prompt("secret prompt")
        self.assertIsNotNone(out)
        self.assertNotIn("secret prompt", out.split("|stdin:")[0])
        self.assertTrue(out.endswith("stdin:secret prompt"))

    def test_long_prompt_survives(self):
        self.env(BRAINLESS_LLM_PROVIDER="claude-cli", BRAINLESS_LLM_MAX_CHARS="1000000")
        with self.fake_claude('wc -c | tr -d " "\n'):
            out = llm.run_prompt("ş" * 200_000)
        self.assertEqual(out, str(len("ş".encode()) * 200_000))

    def test_missing_binary_is_recorded(self):
        self.env(BRAINLESS_LLM_PROVIDER="claude-cli")
        with mock.patch.object(llm, "resolve_claude", return_value=str(Path(self.tmp.name) / "no-such-claude")):
            self.assertIsNone(llm.run_prompt("hi"))
        self.assertIn("FileNotFoundError", self.status.read_text())

    def test_json_envelope_is_unwrapped_and_costed(self):
        self.env(BRAINLESS_LLM_PROVIDER="claude-cli")
        envelope = json.dumps({"type": "result", "is_error": False, "result": "hello there",
                               "total_cost_usd": 0.1234,
                               "usage": {"input_tokens": 3, "output_tokens": 4,
                                         "cache_creation_input_tokens": 18000, "cache_read_input_tokens": 0},
                               "modelUsage": {"claude-opus-4-8": {}}})
        script = "case \"$*\" in *--output-format*json*) ;; *) echo no-json-flag; exit 3;; esac\n"
        script += "cat >/dev/null; printf '%s' '" + envelope + "'\n"
        with self.fake_claude(script):
            self.assertEqual(llm.run_prompt("hi", lane="nightly"), "hello there")
        rows = self.ledger()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["provider"], rows[0]["model"], rows[0]["billed"]),
                         ("claude-cli", "claude-opus-4-8", "plan"))
        self.assertEqual((rows[0]["usd"], rows[0]["cw"]), (0.1234, 18000))

    def test_notice_line_before_the_envelope_is_ignored(self):
        self.env(BRAINLESS_LLM_PROVIDER="claude-cli")
        envelope = json.dumps({"type": "result", "result": "clean answer", "total_cost_usd": 0.02, "usage": {}})
        script = "cat >/dev/null; printf 'mise ~/.config/mise/config.toml tools: claude 9.9.9\\n%s' '" + envelope + "'\n"
        with self.fake_claude(script):
            self.assertEqual(llm.run_prompt("hi"), "clean answer")
        self.assertEqual(self.ledger()[0]["usd"], 0.02)

    def test_json_envelope_error_is_a_failure(self):
        self.env(BRAINLESS_LLM_PROVIDER="claude-cli")
        envelope = json.dumps({"type": "result", "is_error": True, "result": "Credit balance is too low"})
        with self.fake_claude("cat >/dev/null; printf '%s' '" + envelope + "'\n"):
            self.assertIsNone(llm.run_prompt("hi"))
        self.assertIn("\tbilling\t", self.status.read_text())
        self.assertEqual(self.ledger(), [])

    def test_key_in_env_marks_the_call_api_billed(self):
        self.env(BRAINLESS_LLM_PROVIDER="claude-cli", ANTHROPIC_API_KEY="sk-ant-x")
        envelope = json.dumps({"type": "result", "result": "ok", "total_cost_usd": 0.01, "usage": {}})
        with self.fake_claude("cat >/dev/null; printf '%s' '" + envelope + "'\n"):
            llm.run_prompt("hi")
        self.assertEqual(self.ledger()[0]["billed"], "api")


class TestWebLane(ProviderTest):
    def test_web_without_claude_is_skipped(self):
        with mock.patch.object(llm, "resolve", return_value=None), \
             mock.patch.object(llm, "_dispatch") as dispatch:
            self.assertIsNone(llm.run_prompt("search", web=True, lane="research-salvo"))
        dispatch.assert_not_called()
        self.assertIn("web lane needs the claude CLI", self.status.read_text())


if __name__ == "__main__":
    unittest.main()
