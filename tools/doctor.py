#!/usr/bin/env python3
"""`brainless doctor`: is this lite vault able to do its job? One line per check.

    ok    working
    warn  works, but something will bite later (no scheduler, a failed job)
    FAIL  the next run will not work (no model, no key, missing folders)

It reads, it never changes anything. --probe also sends one tiny prompt to the
model, which is the only check that proves the key or sign-in really works.
Exit code 1 when anything FAILs. The full deployment's report is `brainless health`.
"""
import argparse
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import FOLDER_KEYS, folder, vault_root  # noqa: E402
import config  # noqa: E402

KEY_SECRET = {"anthropic": "anthropic_api_key", "openai": "openai_api_key", "grok": "xai_api_key",
              "gemini": "gemini_api_key", "openrouter": "openrouter_api_key"}


def checks(probe=False):
    """Yield (level, message)."""
    import llm
    from resolve_bin import resolve
    vault = vault_root()
    yield ("ok" if sys.version_info >= (3, 11) else "FAIL"), f"Python {sys.version.split()[0]}"
    toml = config.config_path(vault)
    if os.path.exists(toml):
        profile = os.environ.get("BRAINLESS_PROFILE", "(unset)")
        yield ("ok" if profile == "lite" else "warn"), f"brainless.toml, profile {profile}"
    else:
        yield "FAIL", f"no brainless.toml in {vault}; run `brainless init`"

    missing = [folder(k) for k in FOLDER_KEYS if not os.path.isdir(os.path.join(vault, folder(k)))]
    yield ("FAIL" if missing else "ok"), ("missing folders: " + ", ".join(missing) if missing
                                          else "folders " + ", ".join(folder(k) for k in ("inbox", "daily", "library", "thinking")))
    for rel in (os.environ.get("BRAINLESS_SOURCES") or "").split(","):
        if rel.strip() and not os.path.isdir(os.path.join(vault, rel.strip())):
            yield "warn", f"linked folder is gone: {rel.strip()}"

    provider = llm.resolve_provider("compile")
    model = os.environ.get("BRAINLESS_LLM_MODEL", "")
    yield "ok", f"model: {provider}" + (f" / {model}" if model else "")
    if provider in KEY_SECRET or provider == "openai-compatible":
        if provider != "openai-compatible" and not model:
            yield "FAIL", "no model set (brainless.toml [llm] model)"
        if provider in KEY_SECRET and not llm._api_key(provider):
            yield "FAIL", f"no {provider} key; `brainless config secret set {KEY_SECRET[provider]}`"
    elif provider == "ollama":
        models = llm.list_models("ollama")
        if models is None:
            yield "FAIL", "Ollama is not answering; start it (`ollama serve`, or open the Ollama app)"
        elif model and model not in models and f"{model}:latest" not in models:
            yield "FAIL", f"Ollama has no {model}; `ollama pull {model}`"
        else:
            yield "ok", f"Ollama serving {len(models)} model(s)"
    else:
        binary = {"claude-cli": "claude", "codex-cli": "codex", "gemini-cli": "gemini"}.get(provider, provider)
        yield ("ok" if resolve(binary) else "FAIL"), f"{binary} CLI " + ("found" if resolve(binary) else "not installed")

    if probe:
        started = time.time()
        out = llm.run_prompt("Reply with exactly one word, the word OK, and nothing else.",
                             timeout=300, lane="note-classify")
        yield ("ok" if out else "FAIL"), (f"test call answered in {time.time() - started:.0f}s" if out
                                          else "test call failed; see .agents/state/llm_log")

    import schedule
    import subprocess
    kind = schedule.platform()
    if kind == "macos":
        loaded = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{schedule.LABEL}"],
                                capture_output=True).returncode == 0
    elif kind == "linux":
        loaded = subprocess.run(["systemctl", "--user", "is-active", f"{schedule.UNIT}.timer"],
                                capture_output=True).returncode == 0
    else:
        loaded = subprocess.run(["schtasks", "/Query", "/TN", schedule.UNIT], capture_output=True).returncode == 0
    yield ("ok" if loaded else "warn"), ("scheduler entry loaded" if loaded
                                         else "no scheduler entry; runs happen only when you type `brainless tick`")

    import jobqueue
    db = jobqueue.connect()
    failed = db.execute("SELECT kind, last_error FROM jobs WHERE state='failed'").fetchall()
    for row in failed:
        yield "warn", f"job {row['kind']} failed: {row['last_error'][:100]} (`brainless queue retry`)"
    waiting = db.execute("SELECT COUNT(*) FROM jobs WHERE state='queued'").fetchone()[0]
    last = {r["kind"]: r["last_run"] for r in db.execute("SELECT * FROM periodic")}
    if "compile" in last:
        age_h = (time.time() - last["compile"]) / 3600
        when = datetime.fromtimestamp(last["compile"]).strftime("%Y-%m-%d %H:%M")
        yield ("ok" if age_h < 72 else "warn"), f"last compile {when}; {waiting} job(s) waiting"
    else:
        yield "warn", f"no compile has run yet; {waiting} job(s) waiting"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="brainless doctor", description=__doc__.splitlines()[0])
    ap.add_argument("--probe", action="store_true", help="also send one tiny prompt to the model")
    ns = ap.parse_args(argv)
    worst = 0
    for level, msg in checks(ns.probe):
        print(f"{level:>4}  {msg}")
        worst = max(worst, {"ok": 0, "warn": 0, "FAIL": 1}[level])
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
