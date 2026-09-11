"""Minimal, provider-pluggable LLM helper for vault automation scripts.

One entry point: run_prompt(prompt) -> str | None. Context must be embedded
in the prompt. Untrusted content (Telegram messages, transcripts, fetched web
pages, e-mail bodies, calendar invites) flows into these prompts; that is why
claude-cli calls ALWAYS deny the dangerous tools (Bash, Write, Edit, WebFetch
and so on) with --disallowedTools (deny beats allow), so a prompt injection
cannot turn a poisoned note into code execution or data exfiltration. Default:
no tools at all; a tool opens only when the caller passes it explicitly in
allowed_tools (e.g. Read for photo OCR) and it is not on the deny list.
Note: .claude/settings.local.json is no longer tracked; automation does not
inherit the interactive permission list.

The provider is chosen by BRAINLESS_LLM_PROVIDER (default "claude-cli"), so
the whole batch brain can move off Claude by setting a few env vars. Every
caller goes through run_prompt and never names a provider, so switching costs
one file, not six.

  claude-cli        : the Claude Code CLI (`claude -p`). Default; unchanged.
  openai-compatible : any OpenAI-style /chat/completions endpoint, driven by
                      BRAINLESS_LLM_BASE_URL / _API_KEY / _MODEL. Covers xAI
                      Grok (https://api.x.ai/v1), OpenAI, Together, Ollama,
                      LM Studio. Stdlib only, no extra dependencies.

Every real call drops a one-line breadcrumb at .agents/state/llm_status
(timestamp, outcome, detail). health_check reads it so a silent auth expiry
surfaces as red instead of the pipeline looking green while every call 401s.
"""
import json
import os
import subprocess
import urllib.error
import urllib.request
from datetime import datetime

from resolve_bin import resolve_claude

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
STATUS_FILE = os.path.join(VAULT, ".agents", "state", "llm_status")

# Untrusted content enters the automation prompts; these tools are denied on
# every call (code execution, file writes, network access, sub-agents).
_DANGEROUS_TOOLS = [
    "Bash", "Write", "Edit", "MultiEdit", "NotebookEdit",
    "WebFetch", "WebSearch", "Task",
]

# Substrings that mark an authentication/authorization failure in any backend.
_AUTH_HINTS = ("401", "403", "unauthorized", "authenticate",
               "expired", "invalid api key", "invalid_api_key", "authentication")


def _clean(text: str) -> str:
    # House style: em/en dashes are banned in all vault output.
    return text.replace("\u2014", "-").replace("\u2013", "-")


def _record(outcome: str, detail: str = "") -> None:
    """Drop a status breadcrumb for the health check. Best-effort."""
    try:
        os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(STATUS_FILE, "w") as fh:
            fh.write(f"{stamp}\t{outcome}\t{detail[:200].replace(chr(9), ' ')}\n")
    except OSError:
        pass


def _classify(blob: str) -> str:
    low = (blob or "").lower()
    return "auth" if any(h in low for h in _AUTH_HINTS) else "error"


def _run_claude_cli(prompt: str, timeout: int, allowed_tools=None) -> str | None:
    claude = resolve_claude()
    env = os.environ.copy()
    env["PATH"] = os.path.dirname(claude) + os.pathsep + env.get("PATH", "")
    cmd = [claude, "-p", prompt.replace("\x00", "")]  # argv does not accept null bytes
    # The deny list beats allow; whatever settings say, these tools stay closed.
    cmd += ["--disallowedTools", *_DANGEROUS_TOOLS]
    if allowed_tools:
        safe = [t for t in allowed_tools if t not in _DANGEROUS_TOOLS]
        if safe:
            cmd += ["--allowedTools", *safe]
    try:
        r = subprocess.run(
            cmd,
            cwd=VAULT, capture_output=True, text=True, timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        _record("timeout", "claude-cli")
        return None
    if r.returncode != 0:
        blob = (r.stdout or "") + (r.stderr or "")
        _record(_classify(blob), blob.strip()[:200])
        return None
    out = _clean(r.stdout.strip())
    if not out:
        _record("error", "claude-cli empty output")
        return None
    _record("ok", "claude-cli")
    return out


def _run_openai_compatible(prompt: str, timeout: int) -> str | None:
    base = os.environ.get("BRAINLESS_LLM_BASE_URL", "").rstrip("/")
    key = os.environ.get("BRAINLESS_LLM_API_KEY", "")
    model = os.environ.get("BRAINLESS_LLM_MODEL", "")
    if not (base and model):
        _record("error", "openai-compatible: BASE_URL/MODEL unset")
        return None
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4,
    }).encode()
    req = urllib.request.Request(
        f"{base}/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        outcome = "auth" if e.code in (401, 403) else "error"
        _record(outcome, f"openai-compatible HTTP {e.code}")
        return None
    except Exception as e:  # network, timeout, JSON
        _record("error", f"openai-compatible {type(e).__name__}")
        return None
    try:
        out = _clean(data["choices"][0]["message"]["content"].strip())
    except (KeyError, IndexError, TypeError):
        _record("error", "openai-compatible: unexpected response shape")
        return None
    if not out:
        _record("error", "openai-compatible empty output")
        return None
    _record("ok", f"openai-compatible {model}")
    return out


def run_prompt(prompt: str, timeout: int = 300, allowed_tools=None) -> str | None:
    """allowed_tools: opt-in tool grants for claude-cli (e.g. ["Read"] so the
    model can open an image). The openai-compatible provider ignores it."""
    provider = os.environ.get("BRAINLESS_LLM_PROVIDER", "claude-cli").strip()
    if provider == "openai-compatible":
        return _run_openai_compatible(prompt, timeout)
    return _run_claude_cli(prompt, timeout, allowed_tools)
