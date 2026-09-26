"""Minimal, provider-pluggable LLM helper for vault automation scripts.

One entry point: run_prompt(prompt) -> str | None. Context must be embedded
in the prompt. Untrusted content (Telegram messages, transcripts, fetched web
pages, e-mail bodies, calendar invites) flows into these prompts; that is why
claude-cli calls ALWAYS deny the execution tools (Bash, Write, Edit, Task and so
on) with --disallowedTools (deny beats allow), so a prompt injection cannot turn
a poisoned note into code execution or data exfiltration. Default: no tools at
all; a tool opens only when the caller passes it explicitly in allowed_tools
(e.g. Read for photo OCR) and it is not on the deny list.

The research lane: run_prompt(..., web=True) is the ONE exception, added for
weekly_research.py. It moves WebSearch/WebFetch from the deny list to the allow
list so a research pass can actually read the internet. The execution tools stay
denied on that call too, so the worst a poisoned page can do is put a wrong
sentence in a report; it cannot reach the shell or the vault. Callers must treat
everything a web=True call returns as untrusted data and must never feed it back
into a prompt without fencing it. Default stays web=False.
Note: .claude/settings.local.json is no longer tracked; automation does not
inherit the interactive permission list.

The provider is chosen by BRAINLESS_LLM_PROVIDER (default "claude-cli"), so
the whole batch brain can move off Claude by setting a few env vars. Every
caller goes through run_prompt and never names a provider, so switching costs
one file, not six.

Lanes. One global provider was too blunt: the vault has short classification
calls that a 4B model on the worker answers well enough, and long Turkish
summaries that it does not. Every call site therefore passes lane="<name>"
(see LANES below) and each lane can be pointed at its own provider with
BRAINLESS_LLM_PROVIDER_<LANE>, falling back to the global variable. Same
pattern for BRAINLESS_CLAUDE_MODEL_<LANE> and BRAINLESS_GOOSE_MODEL_<LANE>.
Lane names map to env by upper-casing and turning "-" into "_", so lane
"capture-note" reads BRAINLESS_LLM_PROVIDER_CAPTURE_NOTE.

Fallback, and why it is one-directional. BRAINLESS_LLM_FALLBACK[_<LANE>] names
a second provider to try when the first fails, which is what makes a Claude
outage or an expired token survivable: a cloud lane degrades to the local model
instead of returning None. The reverse is refused. A lane deliberately pinned
to a local provider is pinned there for privacy, so its content must never leak
to a cloud provider because the local one timed out; such a fallback is dropped
and logged unless BRAINLESS_LLM_ALLOW_CLOUD_FALLBACK=1 says otherwise.

Local calls are slow (CPU inference on the worker), so their timeout is
multiplied by BRAINLESS_LOCAL_TIMEOUT_FACTOR, default 3, since every caller
tuned its timeout against a cloud model.

  claude-cli        : the Claude Code CLI (`claude -p`). Default. Pinned to
                      Opus 4.8 via --model; override with BRAINLESS_CLAUDE_MODEL
                      (empty string falls back to the CLI default).
  openai-compatible : any OpenAI-style /chat/completions endpoint, driven by
                      BRAINLESS_LLM_BASE_URL / _API_KEY / _MODEL. Covers xAI
                      Grok (https://api.x.ai/v1), OpenAI, Together, Ollama,
                      LM Studio. Stdlib only, no extra dependencies.
  goose             : the Goose CLI (`goose run -t`). Goose is an agent harness,
                      not a model: it needs a provider, but `goose local-models`
                      downloads and serves GGUF models itself, which is how the
                      worker runs fully local inference without an ollama daemon.
                      BRAINLESS_GOOSE_MODEL / _PROVIDER pick the backend.
                      SECURITY: goose ships extensions (the "developer" one can
                      run shell commands). Every call passes --no-profile, which
                      loads NO default extensions, and we never pass a --with-*
                      flag, so the agent has zero tools and can only emit text.
                      That is the goose-side equivalent of --disallowedTools.
                      web=True is therefore NOT supported on this provider.
  ollama, openai,   : shorthands for openai-compatible with a known address
  grok, gemini,       (PRESETS below). Each reads its own key, so a lane on
  openrouter          Grok never receives the OpenAI key: BRAINLESS_<NAME>_API_KEY,
                      else the OS secret store (config.py) under "<name>_api_key".
                      BRAINLESS_<NAME>_BASE_URL moves one, e.g. Ollama on
                      another box. ollama, and openai-compatible pointed at a
                      loopback address, count as local for the fallback rule.
  anthropic         : the Claude API with a key, stdlib HTTP (/v1/messages).
                      Key: BRAINLESS_ANTHROPIC_API_KEY, ANTHROPIC_API_KEY, or the
                      secret store under "anthropic_api_key". Default model
                      claude-opus-5. For people with an API key and no Claude
                      subscription.
  gemini-cli,       : the Gemini and Codex CLIs, signed in with the user's own
  codex-cli           account. Both are agents, so each call runs in an empty
                      temporary directory; Codex also in its read-only sandbox
                      with no session saved. They are weaker boundaries than
                      claude-cli's deny list: a poisoned note could make them
                      read files the user can read. Prefer an API provider for
                      lanes that carry untrusted text (captures, links, mail).
                      gemini-cli is untested here; codex-cli was checked
                      against codex 0.155.

Models. BRAINLESS_LLM_MODEL[_<LANE>] names the model for every provider except
claude-cli (BRAINLESS_CLAUDE_MODEL) and goose (BRAINLESS_GOOSE_MODEL). A model
passed by a caller names a Claude model, so only claude-cli and anthropic use it.

Every real call drops a one-line breadcrumb at .agents/state/llm_status
(timestamp, outcome, detail). health_check reads it so a silent auth expiry
surfaces as red instead of the pipeline looking green while every call 401s.

Ops: `python3 tools/llm.py --lanes` prints every lane with the provider it
currently resolves to, and `--probe <lane>` sends one throwaway prompt through
that lane and reports the wall time, and `--models <provider>` lists what an
HTTP provider serves. All three are read-only.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from resolve_bin import resolve, resolve_claude

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import vault_root  # noqa: E402
import config  # noqa: E402,F401  brainless.toml sets env defaults before anything reads env
VAULT = vault_root()
STATUS_FILE = os.path.join(VAULT, ".agents", "state", "llm_status")
# One line per call, kept short. STATUS_FILE holds only the latest outcome for
# health_check; routing decisions need history to be readable at all.
LOG_FILE = os.path.join(VAULT, ".agents", "state", "llm_log")
LOG_KEEP = 300

# Untrusted content enters the automation prompts; these tools are denied on
# EVERY call, with no opt-in (code execution, file writes, sub-agents).
_NEVER_TOOLS = ["Bash", "Write", "Edit", "MultiEdit", "NotebookEdit", "Task"]
# Network reads. Denied by default, opened only by the research lane (web=True).
_WEB_TOOLS = ["WebFetch", "WebSearch"]
# Kept as the historical name: everything an ordinary call must not touch.
_DANGEROUS_TOOLS = _NEVER_TOOLS + _WEB_TOOLS

# Substrings that mark an authentication/authorization failure in any backend.
_AUTH_HINTS = ("401", "403", "unauthorized", "authenticate",
               "expired", "invalid api key", "invalid_api_key", "authentication")

# Providers that run on the machine itself. A lane pinned to one of these is
# pinned for privacy, so it never falls back to a cloud provider by accident.
# openai-compatible joins them when its address is loopback (see _is_local).
_LOCAL_PROVIDERS = {"goose", "ollama"}

# Shorthand providers: an OpenAI-style endpoint with a known address and its own
# key name in the secret store (None: no key needed).
PRESETS = {
    "ollama":     ("http://localhost:11434/v1", None),
    "openai":     ("https://api.openai.com/v1", "openai_api_key"),
    "grok":       ("https://api.x.ai/v1", "xai_api_key"),
    "gemini":     ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini_api_key"),
    "openrouter": ("https://openrouter.ai/api/v1", "openrouter_api_key"),
}
PROVIDERS = ("claude-cli", "anthropic", "openai-compatible", *PRESETS, "gemini-cli", "codex-cli", "goose")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_DEFAULT_MODEL = "claude-opus-5"

# Every call site, with the shape of its prompt. "short" lanes are the ones a
# small local model can plausibly serve: bounded input, structured output, low
# stakes. "medium" is bounded but not small: worth trying locally, verify the
# output. "long" lanes carry thousands of tokens of Turkish prose and are worth
# a big model. Keep this table in step with the call sites; --lanes prints it.
LANES = {
    "buzz-reply":          "long   read-only answer to an owner Buzz thread reply",
    "capture-note":        "medium voice or text note filed into the vault, note out",
    "capture-link":        "long   summary of a fetched page, up to 12k characters",
    "capture-photo":       "long   photo OCR, needs the Read tool and vision",
    "note-classify":       "short  routing a note to its folder and tags",
    "spiky-actions":       "medium action items from a meeting report, 9k cap, JSON out",
    "meeting-brief":       "long   pre-meeting brief from vault context",
    "compile":             "long   source document to wiki summary",
    "smart-summary":       "long   summary or reading card of a converted book or report",
    "media-clean":         "long   punctuation and speaker turns on a raw transcript chunk, nothing cut",
    "compile-aliases":     "short  3 to 5 alternative names per wiki page, JSON out",
    "compile-concept":     "long   concept page updated in place from new summaries, or summary-to-concept assignment (JSON)",
    "nightly":             "long   nightly digest over the day's notes",
    "resurface":           "long   pick notes worth resurfacing from a catalogue",
    "closeout":            "long   evening close-out over the day",
    "reconcile":           "long   weekly reconcile of projects against dailies",
    "dialectic-topics":    "medium clustering the day's captures into topics, 12k cap",
    "dialectic-persona":   "long   one critical-thinking persona's argument",
    "dialectic-synthesis": "long   synthesis across the five personas",
    "dialectic-connect":   "long   connection scan across the wiki",
    "dialectic-judge":     "short  usable or not, one word per local persona reply, JSON out",
    "dreaming-judge":      "short  link, duplicate or none for two wiki pages, one sentence, JSON out",
    "contradiction-judge": "medium a new summary against its three nearest pages, conflicts as JSON",
    "content":             "long   content engine draft",
    "writing-ideas":       "long   long-form pitch generation for the writing channel",
    "thinker-digest":      "long   digest of a thinker's corpus",
    "thinking-loop":       "long   weekly thinking loop",
    "research-plan":       "long   research question note and hypothesis table",
    "research-salvo":      "web    the one lane that reads the internet",
    "research-synth":      "long   research synthesis",
}


def _model_env(prefix: str, lane: str | None, default: str) -> str:
    """Like _lane_env but an explicitly empty variable means 'the CLI default',
    which is why presence, not truth, decides here."""
    if lane:
        key = prefix + "_" + lane.upper().replace("-", "_")
        if key in os.environ:
            return os.environ[key].strip()
    if prefix in os.environ:
        return os.environ[prefix].strip()
    return default


def _lane_env(prefix: str, lane: str | None) -> str:
    """Read prefix_<LANE> if set, else the plain prefix. '' when neither is set."""
    if lane:
        key = prefix + "_" + lane.upper().replace("-", "_")
        val = os.environ.get(key, "").strip()
        if val:
            return val
    return os.environ.get(prefix, "").strip()


def resolve_provider(lane: str | None = None) -> str:
    """The provider this lane runs on right now. Read-only, used by --lanes too."""
    return _lane_env("BRAINLESS_LLM_PROVIDER", lane) or "claude-cli"


def _resolve_fallback(lane: str | None, primary: str) -> str | None:
    """The provider to try when the primary fails, or None.

    One rule beyond the env lookup: cloud may degrade to local, local may not
    escalate to cloud. A lane pinned local is pinned for privacy, and a timeout
    is not consent to send the same text to a cloud provider instead.
    """
    fb = _lane_env("BRAINLESS_LLM_FALLBACK", lane)
    if not fb or fb.lower() in ("none", "off", "0"):
        return None
    if fb == primary:
        return None
    if _is_local(primary) and not _is_local(fb):
        if os.environ.get("BRAINLESS_LLM_ALLOW_CLOUD_FALLBACK", "").strip() != "1":
            return None
    return fb


def _clean(text: str) -> str:
    # House style: em/en dashes are banned in all vault output.
    return text.replace("\u2014", "-").replace("\u2013", "-")


def _endpoint(provider: str) -> tuple[str, str | None]:
    """(base URL, secret name) for openai-compatible or a preset."""
    if provider in PRESETS:
        base, secret = PRESETS[provider]
        return (os.environ.get(f"BRAINLESS_{provider.upper()}_BASE_URL") or base).rstrip("/"), secret
    return os.environ.get("BRAINLESS_LLM_BASE_URL", "").rstrip("/"), "llm_api_key"


def _is_local(provider: str) -> bool:
    if provider in _LOCAL_PROVIDERS:
        return True
    if provider == "openai-compatible":
        host = urllib.parse.urlsplit(_endpoint(provider)[0]).hostname or ""
        return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".localhost")
    return False


_KEYS: dict = {}


def _api_key(provider: str) -> str:
    """The key for this provider: its own env variable(s), then the OS secret
    store. Never another provider's key. Cached for the process."""
    if provider in _KEYS:
        return _KEYS[provider]
    if provider == "anthropic":
        env_names, secret = ("BRAINLESS_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"), "anthropic_api_key"
    elif provider in PRESETS:
        env_names, secret = (f"BRAINLESS_{provider.upper()}_API_KEY",), PRESETS[provider][1]
    else:
        env_names, secret = ("BRAINLESS_LLM_API_KEY",), "llm_api_key"
    key = next((os.environ[n].strip() for n in env_names if os.environ.get(n, "").strip()), "")
    if not key and secret:
        try:
            key = config.get_secret(secret) or ""
        except Exception:  # a broken keychain must not break the call path
            key = ""
    _KEYS[provider] = key
    return key


# The lane of the call in flight, so the breadcrumb and the log say which one
# it was without threading the name through every runner. The scripts are
# single-threaded batch jobs; there is never more than one call at a time.
_CURRENT_LANE: str | None = None


def _record(outcome: str, detail: str = "") -> None:
    """Drop a status breadcrumb for the health check, and a line in the log.
    Best-effort: a failure to write must never fail the call."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    detail = detail[:200].replace(chr(9), " ")
    lane = _CURRENT_LANE or "-"
    try:
        os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
        with open(STATUS_FILE, "w") as fh:
            fh.write(f"{stamp}\t{outcome}\t[{lane}] {detail}\n")
    except OSError:
        pass
    _log(f"{lane}\t{outcome}\t{detail}")


def _log(line: str) -> None:
    """Append to the rolling call log, trimmed to LOG_KEEP lines.

    The line is stamped when it is written, not when the call started, so the
    log reads in the order things actually happened.
    """
    line = datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "\t" + line
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        old = []
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, errors="replace") as fh:
                old = fh.read().splitlines()[-(LOG_KEEP - 1):]
        with open(LOG_FILE, "w") as fh:
            fh.write("\n".join(old + [line]) + "\n")
    except OSError:
        pass


def _classify(blob: str) -> str:
    low = (blob or "").lower()
    return "auth" if any(h in low for h in _AUTH_HINTS) else "error"


def _run_claude_cli(prompt: str, timeout: int, allowed_tools=None,
                    web: bool = False, model: str | None = None,
                    lane: str | None = None) -> str | None:
    claude = resolve_claude()
    env = os.environ.copy()
    env["PATH"] = os.path.dirname(claude) + os.pathsep + env.get("PATH", "")
    cmd = [claude, "-p", prompt.replace("\x00", "")]  # argv does not accept null bytes
    # Pin the batch brain to a specific model so the pipeline does not silently
    # drift when the CLI default changes. Overridable via env; empty string means
    # "use the CLI default" (do not pass --model at all).
    model = (model or _model_env("BRAINLESS_CLAUDE_MODEL", lane, "claude-opus-4-8")).strip()
    if model:
        cmd += ["--model", model]
    # The deny list beats allow; whatever settings say, these tools stay closed.
    # web=True moves only the two network readers across; execution never moves.
    denied = list(_NEVER_TOOLS) if web else list(_DANGEROUS_TOOLS)
    cmd += ["--disallowedTools", *denied]
    granted = list(allowed_tools or [])
    if web:
        granted += _WEB_TOOLS
    safe = [x for x in dict.fromkeys(granted) if x not in denied]
    if safe:
        cmd += ["--allowedTools", *safe]
    try:
        r = subprocess.run(
            cmd,
            cwd=VAULT, capture_output=True, text=True, timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        _record("timeout", "claude-cli web" if web else "claude-cli")
        return None
    if r.returncode != 0:
        blob = (r.stdout or "") + (r.stderr or "")
        _record(_classify(blob), blob.strip()[:200])
        return None
    out = _clean(r.stdout.strip())
    if not out:
        _record("error", "claude-cli empty output")
        return None
    _record("ok", "claude-cli web" if web else "claude-cli")
    return out


def _run_goose(prompt: str, timeout: int, model: str | None = None,
               lane: str | None = None) -> str | None:
    """Goose CLI in one-shot mode. Used for local inference on the worker.

    Flag-by-flag, because each one is load-bearing:
      --no-profile   load none of the user's default extensions. This is the
                     security boundary: with no extension there is no tool, so a
                     poisoned note cannot reach a shell. Never add --with-*.
      --no-session   do not write a session file for an automated run.
      --max-turns 1  one pass, no agentic loop; we want a completion, not an agent.
      -q             only the model response on stdout, so the caller can parse it.
    """
    goose_bin = shutil.which("goose") or os.path.expanduser("~/.local/bin/goose")
    if not os.path.exists(goose_bin):
        _record("error", "goose: binary not found")
        return None
    cmd = [goose_bin, "run", "--no-profile", "--no-session", "--max-turns", "1",
           "-q", "-t", prompt.replace("\x00", "")]
    provider = _lane_env("BRAINLESS_GOOSE_PROVIDER", lane)
    model = (model or _model_env("BRAINLESS_GOOSE_MODEL", lane, "")).strip()
    if provider:
        cmd += ["--provider", provider]
    if model:
        cmd += ["--model", model]
    try:
        r = subprocess.run(cmd, cwd=VAULT, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        _record("timeout", "goose")
        return None
    except OSError as e:
        _record("error", f"goose {type(e).__name__}")
        return None
    if r.returncode != 0:
        blob = (r.stdout or "") + (r.stderr or "")
        _record(_classify(blob), ("goose: " + blob.strip())[:200])
        return None
    out = _clean(r.stdout.strip())
    if not out:
        _record("error", "goose empty output")
        return None
    _record("ok", f"goose {model or 'default'}")
    return out


def _run_openai_compatible(prompt: str, timeout: int, provider: str = "openai-compatible",
                           lane: str | None = None) -> str | None:
    base, secret = _endpoint(provider)
    key = _api_key(provider) if secret else ""
    model = _model_env("BRAINLESS_LLM_MODEL", lane, "")
    if not (base and model):
        _record("error", f"{provider}: base URL or model unset (BRAINLESS_LLM_MODEL)")
        return None
    if provider in PRESETS and secret and not key:
        _record("auth", f"{provider}: no key (brainless config secret set {secret})")
        return None
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4,
    }).encode()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(f"{base}/chat/completions", data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        outcome = "auth" if e.code in (401, 403) else "error"
        _record(outcome, f"{provider} HTTP {e.code}")
        return None
    except Exception as e:  # network, timeout, JSON
        _record("error", f"{provider} {type(e).__name__}")
        return None
    try:
        out = _clean((data["choices"][0]["message"]["content"] or "").strip())
    except (KeyError, IndexError, TypeError):
        _record("error", f"{provider}: unexpected response shape")
        return None
    if not out:
        _record("error", f"{provider} empty output")
        return None
    _record("ok", f"{provider} {model}")
    return out


def _run_anthropic(prompt: str, timeout: int, model: str | None = None,
                   lane: str | None = None) -> str | None:
    """The Claude API over stdlib HTTP. One retry on 429/5xx, honouring a
    short retry-after, because an overloaded minute should not cost a night."""
    key = _api_key("anthropic")
    if not key:
        _record("auth", "anthropic: no key (brainless config secret set anthropic_api_key)")
        return None
    model = (model or _model_env("BRAINLESS_LLM_MODEL", lane, "") or ANTHROPIC_DEFAULT_MODEL).strip()
    payload = {"model": model,
               "max_tokens": int(os.environ.get("BRAINLESS_LLM_MAX_TOKENS", "16000")),
               "messages": [{"role": "user", "content": prompt}]}
    url = os.environ.get("BRAINLESS_ANTHROPIC_BASE_URL", "").rstrip("/")
    url = f"{url}/v1/messages" if url else ANTHROPIC_URL
    headers = {"Content-Type": "application/json", "x-api-key": key,
               "anthropic-version": "2023-06-01"}
    data = None
    for attempt in (1, 2):
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.load(resp)
            break
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                _record("auth", f"anthropic HTTP {e.code}")
                return None
            if attempt == 1 and (e.code == 429 or e.code >= 500):
                try:
                    wait = float(e.headers.get("retry-after") or 5)
                except ValueError:
                    wait = 5.0
                time.sleep(min(max(wait, 1.0), 30.0))
                continue
            _record("error", f"anthropic HTTP {e.code}")
            return None
        except Exception as e:  # network, timeout, JSON
            _record("error", f"anthropic {type(e).__name__}")
            return None
    if not isinstance(data, dict):
        _record("error", "anthropic: no response")
        return None
    if data.get("stop_reason") == "refusal":
        _record("error", "anthropic: refused")
        return None
    out = _clean("".join(b.get("text", "") for b in data.get("content") or []
                         if isinstance(b, dict) and b.get("type") == "text").strip())
    if not out:
        _record("error", "anthropic empty output")
        return None
    if data.get("stop_reason") == "max_tokens":
        _log(f"{_CURRENT_LANE or '-'}\twarn\tanthropic hit max_tokens, output truncated")
    _record("ok", f"anthropic {model}")
    return out


def _run_agent_cli(provider: str, prompt: str, timeout: int,
                   lane: str | None = None) -> str | None:
    """gemini-cli / codex-cli, signed in with the user's own account.

    Each call starts in an empty temporary directory, so the agent's working
    root holds nothing, and the prompt goes in on stdin, never argv.
    """
    name = "gemini" if provider == "gemini-cli" else "codex"
    binary = resolve(name)
    if not binary:
        _record("error", f"{provider}: `{name}` not installed")
        return None
    model = _model_env("BRAINLESS_LLM_MODEL", lane, "")
    with tempfile.TemporaryDirectory(prefix="brainless-llm-") as work:
        out_file = os.path.join(work, ".last-message")
        if provider == "codex-cli":
            cmd = [binary, "exec", "--sandbox", "read-only", "--skip-git-repo-check",
                   "--ephemeral", "--color", "never", "-C", work, "-o", out_file]
        else:
            cmd = [binary]
        if model:
            cmd += ["-m", model]
        cmd += ["-"] if provider == "codex-cli" else ["-p", ""]
        try:
            r = subprocess.run(cmd, cwd=work, input=prompt.replace("\x00", ""),
                               capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            _record("timeout", provider)
            return None
        except OSError as e:
            _record("error", f"{provider} {type(e).__name__}")
            return None
        if r.returncode != 0:
            blob = (r.stdout or "") + (r.stderr or "")
            # Agent CLIs print a banner first; the reason for the failure is at the end.
            _record(_classify(blob), f"{provider}: " + " ".join(blob.split())[-180:])
            return None
        text = r.stdout
        if provider == "codex-cli":
            try:
                with open(out_file, encoding="utf-8") as fh:
                    text = fh.read()
            except OSError:
                pass  # older codex: the final message is on stdout
    out = _clean(text.strip())
    if not out:
        _record("error", f"{provider} empty output")
        return None
    _record("ok", f"{provider} {model or 'default'}")
    return out


def _local_timeout(timeout: int) -> int:
    """Caller timeouts were tuned against a cloud model. CPU inference on the
    worker is an order slower, so stretch them rather than make every caller
    carry two numbers."""
    try:
        factor = float(os.environ.get("BRAINLESS_LOCAL_TIMEOUT_FACTOR", "3"))
    except ValueError:
        factor = 3.0
    return max(timeout, int(timeout * max(factor, 1.0)))


def _dispatch(provider: str, prompt: str, timeout: int, allowed_tools,
              web: bool, model: str | None, lane: str | None) -> str | None:
    if _is_local(provider):
        timeout = _local_timeout(timeout)
        # A caller-supplied model names a Claude model (the research passes ask
        # for Opus 5). It means nothing to a local backend, so drop it and let
        # BRAINLESS_GOOSE_MODEL[_<LANE>] decide.
        model = None
    if provider == "openai-compatible" or provider in PRESETS:
        return _run_openai_compatible(prompt, timeout, provider, lane=lane)
    if provider == "anthropic":
        return _run_anthropic(prompt, timeout, model, lane=lane)
    if provider in ("gemini-cli", "codex-cli"):
        return _run_agent_cli(provider, prompt, timeout, lane=lane)
    if provider == "goose":
        return _run_goose(prompt, timeout, model, lane=lane)
    if provider != "claude-cli":
        _record("error", f"unknown provider {provider!r}; one of: {', '.join(PROVIDERS)}")
        return None
    return _run_claude_cli(prompt, timeout, allowed_tools, web=web, model=model, lane=lane)


def run_prompt(prompt: str, timeout: int = 300, allowed_tools=None,
               web: bool = False, model: str | None = None,
               lane: str | None = None) -> str | None:
    """allowed_tools: opt-in tool grants for claude-cli (e.g. ["Read"] so the
    model can open an image). The other providers ignore it.

    web: open WebSearch/WebFetch for this one call (the research lane). Only
    claude-cli can do this, so a web call is routed to claude-cli whatever the
    lane is configured for, and the breadcrumb says so. A research pass must
    never believe it searched the web when it did not, and a lane that asked
    for the internet has already accepted that the internet is involved.

    model: override the provider's pinned model for this call, e.g. the research
    passes asking for Opus 5 while the rest of the pipeline stays on 4.8. It is
    ignored on local providers, which pick their model from the environment.

    lane: the name of this call site, from LANES. It selects the per-lane
    provider, model and fallback. Callers should always pass one; a call with no
    lane simply follows the global settings.
    """
    global _CURRENT_LANE
    _CURRENT_LANE = lane
    primary = resolve_provider(lane)
    if web and primary != "claude-cli":
        _log(f"{lane or '-'}\troute\tweb call forced to claude-cli from {primary}")
        primary = "claude-cli"
    if web and not resolve("claude"):
        # Only claude-cli can read the web here. Without it the research lane is
        # skipped with a clear note, never silently answered from memory.
        _record("error", "web lane needs the claude CLI, which is not installed; skipped")
        _CURRENT_LANE = None
        return None
    try:
        started = time.time()
        out = _dispatch(primary, prompt, timeout, allowed_tools, web, model, lane)
        _log(f"{lane or '-'}\ttiming\t{primary} {int(time.time() - started)}s "
             f"in={len(prompt)}c out={len(out or '')}c")
        if out is not None:
            return out
        fallback = _resolve_fallback(lane, primary)
        if not fallback:
            return None
        _log(f"{lane or '-'}\troute\t{primary} failed, falling back to {fallback}")
        started = time.time()
        out = _dispatch(fallback, prompt, timeout, allowed_tools, web, model, lane)
        _log(f"{lane or '-'}\ttiming\t{fallback} {int(time.time() - started)}s "
             f"in={len(prompt)}c out={len(out or '')}c")
        return out
    finally:
        _CURRENT_LANE = None


def list_models(provider: str) -> list[str] | None:
    """Model ids an HTTP provider offers (GET <base>/models), or None when it
    cannot say. The installer uses it to offer a pick instead of a blank line."""
    if provider == "anthropic":
        base = os.environ.get("BRAINLESS_ANTHROPIC_BASE_URL", "").rstrip("/") or "https://api.anthropic.com"
        url, key = f"{base}/v1/models", _api_key("anthropic")
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    elif provider == "openai-compatible" or provider in PRESETS:
        base, secret = _endpoint(provider)
        if not base:
            return None
        url, key = f"{base}/models", _api_key(provider) if secret else ""
        headers = {"Authorization": f"Bearer {key}"} if key else {}
    else:
        return None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as resp:
            data = json.load(resp)
    except Exception:
        return None
    rows = data.get("data") if isinstance(data, dict) else None
    return sorted(str(r["id"]) for r in rows or [] if isinstance(r, dict) and r.get("id"))


def _cli() -> int:
    """Read-only operator helpers. --lanes shows routing, --probe times a lane."""
    import sys
    args = sys.argv[1:]
    if args and args[0] == "--lanes":
        width = max(len(k) for k in LANES)
        print(f"global provider: {os.environ.get('BRAINLESS_LLM_PROVIDER', 'claude-cli')}")
        print(f"global fallback: {os.environ.get('BRAINLESS_LLM_FALLBACK', '(none)')}")
        print()
        for lane, shape in LANES.items():
            provider = resolve_provider(lane)
            fallback = _resolve_fallback(lane, provider)
            tail = f" -> {fallback}" if fallback else ""
            print(f"{lane:<{width}}  {provider}{tail:<18}  {shape}")
        return 0
    if len(args) == 2 and args[0] == "--probe":
        lane = args[1]
        if lane not in LANES:
            print(f"unknown lane: {lane}. Try --lanes.")
            return 2
        prompt = ("Reply with exactly one word, the word OK, and nothing else. "
                  "Do not explain.")
        started = time.time()
        out = run_prompt(prompt, timeout=120, lane=lane)
        secs = time.time() - started
        print(f"lane={lane} provider={resolve_provider(lane)} {secs:.1f}s")
        print(f"reply: {(out or '(no answer)')[:200]}")
        return 0 if out else 1
    if len(args) == 2 and args[0] == "--models":
        models = list_models(args[1])
        if models is None:
            print(f"{args[1]}: no model list (not an HTTP provider, unreachable, or no key)")
            return 1
        print("\n".join(models))
        return 0
    print(__doc__.strip().splitlines()[0])
    print("usage: llm.py --lanes | --probe <lane> | --models <provider>")
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
