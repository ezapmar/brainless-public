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

Every real call drops a one-line breadcrumb at .agents/state/llm_status
(timestamp, outcome, detail). health_check reads it so a silent auth expiry
surfaces as red instead of the pipeline looking green while every call 401s.

Ops: `python3 tools/llm.py --lanes` prints every lane with the provider it
currently resolves to, and `--probe <lane>` sends one throwaway prompt through
that lane and reports the wall time. Both are read-only.
"""
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime

from resolve_bin import resolve_claude

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
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
_LOCAL_PROVIDERS = {"goose"}

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
    if primary in _LOCAL_PROVIDERS and fb not in _LOCAL_PROVIDERS:
        if os.environ.get("BRAINLESS_LLM_ALLOW_CLOUD_FALLBACK", "").strip() != "1":
            return None
    return fb


def _clean(text: str) -> str:
    # House style: em/en dashes are banned in all vault output.
    return text.replace("\u2014", "-").replace("\u2013", "-")


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
    if provider in _LOCAL_PROVIDERS:
        timeout = _local_timeout(timeout)
        # A caller-supplied model names a Claude model (the research passes ask
        # for Opus 5). It means nothing to a local backend, so drop it and let
        # BRAINLESS_GOOSE_MODEL[_<LANE>] decide.
        model = None
    if provider == "openai-compatible":
        return _run_openai_compatible(prompt, timeout)
    if provider == "goose":
        return _run_goose(prompt, timeout, model, lane=lane)
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
    print(__doc__.strip().splitlines()[0])
    print("usage: llm.py --lanes | --probe <lane>")
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
