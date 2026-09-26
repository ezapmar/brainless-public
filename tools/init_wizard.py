#!/usr/bin/env python3
"""`brainless init`: set up a lite vault in this checkout, one question at a time.

  1. checks Python and where the vault sits (macOS asks background jobs for
     permission inside ~/Documents, ~/Desktop and iCloud Drive, so it warns)
  2. your name and the language brainless writes in
  3. folders, named in that language, plus any existing notes folders to read
     (linked in, never copied or changed)
  4. the model: local (Ollama), a cloud API key (kept in the OS keychain), or a
     CLI you are already signed in to (claude, codex, gemini); then one test call
  5. optional extras (semantic search)
  6. brainless.toml, written last-but-one so every answer lands in one file
  7. the scheduler entry, and the OS permissions it needs
  8. a first note and a first run, if you want to see it work

Every question has a default, so `brainless init --yes` (with --provider and
friends) runs unattended, which is how CI tests it. Answers are read from the
terminal even when the installer arrived through `curl | bash`. Re-running is
safe: an existing brainless.toml is kept unless you say otherwise.
"""
import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import FOLDER_KEYS, skeleton, vault_root  # noqa: E402
import config  # noqa: E402

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROTECTED = ("~/Documents", "~/Desktop", "~/Library/Mobile Documents")
KEY_PROVIDERS = {  # provider -> (label, secret name, suggested model or None)
    "anthropic":  ("Claude API (Anthropic)", "anthropic_api_key", "claude-opus-5"),
    "openai":     ("OpenAI", "openai_api_key", None),
    "grok":       ("Grok (xAI)", "xai_api_key", None),
    "gemini":     ("Gemini API (Google)", "gemini_api_key", None),
    "openrouter": ("OpenRouter", "openrouter_api_key", None),
}
CLI_PROVIDERS = {"claude-cli": ("claude", "Claude Code"), "codex-cli": ("codex", "Codex"),
                 "gemini-cli": ("gemini", "Gemini CLI")}
OLLAMA_MODEL = "qwen3:8b"


class Prompter:
    """Questions on the terminal; defaults only with --yes. Tests pass `answers`."""

    def __init__(self, assume_yes=False, answers=None):
        self.assume_yes = assume_yes
        self.answers = list(answers) if answers is not None else None
        self.tty = None
        if answers is None and not assume_yes and not sys.stdin.isatty():
            try:
                self.tty = open("/dev/tty", "r+")
            except OSError:
                self.assume_yes = True

    def _read(self, prompt, secret=False):
        if self.answers is not None:
            return self.answers.pop(0) if self.answers else ""
        if self.tty:
            if secret:
                return getpass.getpass(prompt, stream=self.tty)
            self.tty.write(prompt)
            self.tty.flush()
            return self.tty.readline().rstrip("\n")
        return getpass.getpass(prompt) if secret else input(prompt)

    def ask(self, q, default=""):
        if self.assume_yes:
            return default
        got = self._read(f"  {q}" + (f" [{default}]" if default else "") + ": ").strip()
        return got or default

    def secret(self, q):
        if self.assume_yes:
            return ""
        return self._read(f"  {q} (hidden): ", secret=True).strip()

    def yes(self, q, default=True):
        if self.assume_yes:
            return default
        got = self._read(f"  {q} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
        return default if not got else got.startswith(("y", "e"))   # e: evet

    def choose(self, q, options, default=0):
        """options: list of (value, label). Returns the value."""
        if self.assume_yes:
            return options[default][0]
        say(q)
        for i, (_, label) in enumerate(options, 1):
            print(f"    {i}. {label}")
        while True:
            got = self._read(f"  choice [{default + 1}]: ").strip()
            if not got:
                return options[default][0]
            if got.isdigit() and 1 <= int(got) <= len(options):
                return options[int(got) - 1][0]
            if self.answers is not None:
                raise ValueError(f"bad scripted choice {got!r}")
            print("    pick a number from the list")


def say(msg):
    print(f"\033[1m==> {msg}\033[0m" if sys.stdout.isatty() else f"==> {msg}")


def ok(msg):
    print(f"    ok: {msg}")


def warn(msg):
    print(f"    ! {msg}")


# ─── steps ────────────────────────────────────────────────────────

def check_python():
    if sys.version_info < (3, 11):
        raise SystemExit("brainless needs Python 3.11 or newer (for tomllib)")
    ok(f"Python {sys.version.split()[0]}")


def check_location(vault):
    real = os.path.realpath(vault)
    for p in PROTECTED:
        if sys.platform == "darwin" and real.startswith(os.path.realpath(os.path.expanduser(p)) + os.sep):
            warn(f"{vault} is inside {p}. macOS asks every background job for access there, "
                 "so scheduled runs may stall on a permission prompt. ~/brainless avoids that.")
            return True
    ok(f"vault at {vault}")
    return False


def ask_profile(pr, args):
    name = args.name or pr.ask("Your first name (used in prompts)", os.environ.get("USER", "").title() or "me")
    lang = (args.lang or pr.ask("Language brainless writes in (en, tr, de, ...)", "en")).lower()
    if not lang.isalpha() or not 2 <= len(lang) <= 3:
        warn(f"{lang!r} is not a language code; using en")
        lang = "en"
    return name, lang


def ask_folders(pr, args, lang, vault):
    names = skeleton(lang)
    say("Folders")
    for key in ("inbox", "daily", "library", "thinking"):
        print(f"    {key:<9} {names[key]}/")
    if not args.yes and pr.yes("Rename any of them?", False):
        for key in ("inbox", "daily", "library", "thinking"):
            new = pr.ask(f"{key} folder", names[key])
            if new != names[key] and key == "thinking":
                for child in ("beliefs", "decisions", "ideas"):
                    names[child] = new + names[child][len(names["thinking"]):]
            names[key] = new
    for key in FOLDER_KEYS:
        os.makedirs(os.path.join(vault, names[key]), exist_ok=True)
    ok("created " + ", ".join(f"{names[k]}/" for k in ("inbox", "daily", "library", "thinking")))
    keep_out_of_git(vault, names)
    print("    Obsidian is optional: open this folder as a vault if you like, or use any editor.")

    extra = list(args.extra or [])
    if not args.yes:
        while pr.yes("Read notes from another folder too (an old Obsidian vault, a notes folder)?", False):
            path = pr.ask("Folder path")
            if path:
                extra.append(path)
    sources = []
    for path in extra:
        rel = link_external(vault, names["library"], path)
        if rel:
            sources.append(rel)
    return names, sources


def keep_out_of_git(vault, names):
    """Localised folders are not in the repo's .gitignore (it knows the English
    names), so list them in .git/info/exclude: local to this clone, never pushed,
    and a `git add .` cannot pick up a note by mistake."""
    exclude = os.path.join(vault, ".git", "info", "exclude")
    if not os.path.isdir(os.path.dirname(exclude)):
        return
    try:
        with open(exclude, encoding="utf-8") as fh:
            have = set(fh.read().splitlines())
    except OSError:
        have = set()
    want = [f"/{names[k].split('/')[0]}/" for k in ("inbox", "daily", "library", "thinking")]
    new = [w for w in dict.fromkeys(want) if w not in have]
    if new:
        with open(exclude, "a", encoding="utf-8") as fh:
            fh.write("# brainless init: your notes stay out of git\n" + "\n".join(new) + "\n")
        ok("kept out of git: " + " ".join(new))


def link_external(vault, library, path):
    """Link an outside folder under <library>/_linked/ so the compiler reads its .md
    files as vault sources. The folder itself is never written to."""
    src = os.path.realpath(os.path.expanduser(path))
    if not os.path.isdir(src):
        warn(f"not a folder, skipped: {path}")
        return None
    if src == os.path.realpath(vault) or src.startswith(os.path.realpath(vault) + os.sep):
        warn(f"already inside the vault, nothing to link: {path}")
        return None
    rel = f"{library}/_linked/{os.path.basename(src.rstrip(os.sep)) or 'notes'}"
    dst = os.path.join(vault, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.islink(dst) and os.path.realpath(dst) == src:
        ok(f"{rel} already links to {src}")
        return rel
    if os.path.lexists(dst):
        warn(f"{rel} exists and points elsewhere; skipped")
        return None
    os.symlink(src, dst, target_is_directory=True)
    ok(f"{rel} -> {src} (read only: its .md files are summarised, nothing is written there)")
    return rel


def ask_llm(pr, args):
    """Returns (llm settings dict, secret to store or None)."""
    import llm
    provider = args.provider
    if not provider:
        kind = pr.choose("Which model should brainless use?", [
            ("local", "On this machine with Ollama (private, free, slower)"),
            ("key", "A cloud API with a key (Claude, OpenAI, Grok, Gemini, OpenRouter)"),
            ("cli", "A CLI I am already signed in to (Claude Code, Codex, Gemini CLI)"),
        ])
        if kind == "local":
            provider = "ollama"
        elif kind == "key":
            provider = pr.choose("Which API?", [(p, v[0]) for p, v in KEY_PROVIDERS.items()])
        else:
            found = [(p, f"{label}" + ("" if llm_resolve(binary) else "  (not installed)"))
                     for p, (binary, label) in CLI_PROVIDERS.items()]
            provider = pr.choose("Which CLI?", found)
    if provider not in llm.PROVIDERS:
        raise SystemExit(f"unknown provider {provider!r}; one of {', '.join(llm.PROVIDERS)}")
    settings, secret = {"provider": provider}, None

    if provider == "ollama":
        model = setup_ollama(pr, args)
        if model:
            settings["model"] = model
    elif provider in KEY_PROVIDERS:
        label, secret_name, suggested = KEY_PROVIDERS[provider]
        existing = config.get_secret(secret_name)
        key = os.environ.get(args.api_key_env, "") if args.api_key_env else ""
        if not key and existing and pr.yes(f"A {label} key is already stored. Keep it?", True):
            key = None
        elif not key:
            key = pr.secret(f"{label} API key")
        if key:
            secret = (secret_name, key)
            os.environ[f"BRAINLESS_{provider.upper()}_API_KEY" if provider != "anthropic"
                       else "BRAINLESS_ANTHROPIC_API_KEY"] = key   # for the test call only
        elif key == "" and not existing:
            warn("no key given; add it later with: brainless config secret set " + secret_name)
        settings["model"] = args.model or pick_model(pr, provider, suggested)
    elif provider in CLI_PROVIDERS:
        binary, label = CLI_PROVIDERS[provider]
        if not llm_resolve(binary):
            warn(f"{label} is not installed. Install it, run `{binary}` once to sign in, "
                 "then run `brainless init` again.")
        else:
            ok(f"{label} found; if it has never been used here, run `{binary}` once to sign in")
        if args.model:
            settings["model"] = args.model
    else:
        if args.model:
            settings["model"] = args.model
        if args.base_url:
            settings["base_url"] = args.base_url
    return settings, secret


def llm_resolve(binary):
    from resolve_bin import resolve
    return resolve(binary)


def pick_model(pr, provider, suggested):
    import llm
    models = llm.list_models(provider) or []
    if suggested and (not models or suggested in models):
        return pr.ask("Model", suggested)
    if models:
        shown = models[:15]
        print("    available: " + ", ".join(shown) + (" ..." if len(models) > 15 else ""))
        return pr.ask("Model", shown[0])
    return pr.ask("Model name (see your provider's model list)", suggested or "")


def setup_ollama(pr, args):
    import llm
    binary = llm_resolve("ollama")
    if not binary:
        cmd = (["brew", "install", "ollama"] if sys.platform == "darwin"
               else ["sh", "-c", "curl -fsSL https://ollama.com/install.sh | sh"])
        shown = " ".join(cmd) if cmd[0] == "brew" else cmd[2]
        if sys.platform.startswith("win"):
            warn("install Ollama from https://ollama.com/download, then run brainless init again")
            return args.model or OLLAMA_MODEL
        if cmd[0] == "brew" and not shutil.which("brew"):
            warn("Ollama is not installed and Homebrew is missing; get it from https://ollama.com/download")
            return args.model or OLLAMA_MODEL
        if args.install_ollama or pr.yes(f"Ollama is not installed. Install it now ({shown})?", False):
            if subprocess.run(cmd).returncode != 0:
                warn("the Ollama install failed; install it by hand and run brainless init again")
                return args.model or OLLAMA_MODEL
            binary = llm_resolve("ollama")
        else:
            print(f"    install later with: {shown}")
            return args.model or OLLAMA_MODEL
    if llm.list_models("ollama") is None:
        if sys.platform == "darwin" and shutil.which("brew"):
            subprocess.run(["brew", "services", "start", "ollama"], capture_output=True)
        else:
            subprocess.Popen([binary, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        import time
        for _ in range(10):
            time.sleep(1)
            if llm.list_models("ollama") is not None:
                break
    have = llm.list_models("ollama") or []
    if have:
        print("    installed models: " + ", ".join(have))
    model = args.model or pr.ask("Model", have[0] if have else OLLAMA_MODEL)
    if model not in have and binary:
        if args.yes or pr.yes(f"Download {model} now (several GB)?", True):
            subprocess.run([binary, "pull", model])
    return model


def probe(settings):
    """One real call with the chosen settings. True when the model answered."""
    import llm
    os.environ["BRAINLESS_LLM_PROVIDER"] = settings["provider"]
    if "model" in settings:
        os.environ["BRAINLESS_LLM_MODEL"] = settings["model"]
    if "base_url" in settings:
        os.environ["BRAINLESS_LLM_BASE_URL"] = settings["base_url"]
    llm._KEYS.clear()
    out = llm.run_prompt("Reply with exactly one word, the word OK, and nothing else.",
                         timeout=300, lane="note-classify")
    if out:
        ok(f"the model answered: {out.strip()[:40]}")
        return True
    detail = ""
    try:
        with open(llm.STATUS_FILE) as fh:
            detail = fh.read().strip().split("\t", 2)[-1]
    except OSError:
        pass
    warn(f"no answer from {settings['provider']}: {detail or 'see .agents/state/llm_log'}")
    return False


def install_extras(pr, args):
    want_search = args.search if args.search is not None else pr.yes(
        "Install semantic search (better search, about 200 MB)?", False)
    if want_search:
        req = os.path.join(ENGINE, "requirements-search.txt")
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "-r", req])
        (ok if r.returncode == 0 else warn)("semantic search " + ("installed" if r.returncode == 0 else "failed to install"))


def toml_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    return json.dumps(str(v), ensure_ascii=False)


def render_toml(name, lang, folders, sources, llm_settings, schedule):
    lines = ["# Written by `brainless init`. Edit freely; `brainless config show` prints what it sets.",
             "# Keys are not here: they are in the OS keychain (`brainless config secret`).", "",
             'profile = "lite"', "", "[owner]", f"name = {toml_value(name)}", f"lang = {toml_value(lang)}",
             "", "[folders]"]
    lines += [f"{k} = {toml_value(folders[k])}" for k in FOLDER_KEYS]
    if sources:
        lines.append(f"sources = {toml_value(sources)}")
    lines += ["", "[llm]"] + [f"{k} = {toml_value(v)}" for k, v in llm_settings.items()]
    lines += ["", "[schedule]", f"enabled = {toml_value(schedule)}", ""]
    return "\n".join(lines)


def setup_schedule(pr, args, vault, in_protected):
    import schedule
    want = args.schedule if args.schedule is not None else pr.yes(
        "Run brainless in the background (every 15 minutes, and at login)?", True)
    if not want:
        print("    later: brainless schedule install")
        return False
    os.environ["BRAINLESS_PROFILE"] = "lite"
    if schedule.install() != 0:
        warn("the scheduler could not be installed; try `brainless schedule install` later")
        return False
    if sys.platform == "darwin" and in_protected:
        print("    macOS needs Full Disk Access for Python to run there in the background.")
        if pr.yes("Open System Settings > Privacy > Full Disk Access now?", True):
            subprocess.run(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"])
            pr.ask("Add your Python (" + schedule.python_bin() + "), then press Enter")
    if sys.platform.startswith("linux") and shutil.which("loginctl"):
        if args.linger or (not args.yes and pr.yes("Keep running while you are logged out (loginctl enable-linger)?", False)):
            subprocess.run(["loginctl", "enable-linger"])
    return True


def first_run(pr, args, lang):
    import jobqueue
    if not (args.first_run if args.first_run is not None else pr.yes(
            "Write a first note and run the digest and compile now (uses the model)?", False)):
        return
    text = {"tr": "brainless kuruldu. İlk not: bu klasörde düşüncelerimi toplayacağım."}.get(
        lang, "brainless is set up. First note: this is where my thinking goes.")
    ok("note: " + jobqueue.capture_text(text))
    db = jobqueue.connect()
    for kind in ("digest", "compile"):
        jobqueue.enqueue(db, kind)
    jobqueue.tick()


def parse(argv=None):
    ap = argparse.ArgumentParser(prog="brainless init", description="Set up a lite vault.")
    ap.add_argument("--yes", action="store_true", help="take every default; no questions")
    ap.add_argument("--name")
    ap.add_argument("--lang")
    ap.add_argument("--extra", action="append", help="another notes folder to read (repeatable)")
    ap.add_argument("--provider", help="skip the model question: ollama, anthropic, openai, grok, "
                                       "gemini, openrouter, claude-cli, codex-cli, gemini-cli, ...")
    ap.add_argument("--model")
    ap.add_argument("--base-url", help="for provider openai-compatible")
    ap.add_argument("--api-key-env", help="read the API key from this environment variable")
    ap.add_argument("--install-ollama", action="store_true")
    ap.add_argument("--no-probe", action="store_true", help="skip the test call")
    ap.add_argument("--search", action=argparse.BooleanOptionalAction, default=None)
    ap.add_argument("--schedule", action=argparse.BooleanOptionalAction, default=None)
    ap.add_argument("--linger", action="store_true")
    ap.add_argument("--first-run", action=argparse.BooleanOptionalAction, default=None)
    ap.add_argument("--force", action="store_true", help="overwrite an existing brainless.toml")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse(argv)
    return run(args, Prompter(args.yes))


def run(args, pr):
    vault = vault_root()
    say("brainless init")
    check_python()
    in_protected = check_location(vault)
    toml = config.config_path(vault)
    if os.path.exists(toml) and not args.force:
        if args.yes or not pr.yes("brainless.toml exists. Start over and replace it?", False):
            ok("kept the existing brainless.toml; `brainless doctor` checks it")
            return 0
    say("About you")
    name, lang = ask_profile(pr, args)
    folders, sources = ask_folders(pr, args, lang, vault)
    say("Model")
    llm_settings, secret = ask_llm(pr, args)
    if secret:
        backend = config.set_secret(*secret)
        ok(f"key stored in the {backend.replace('keychain', 'macOS Keychain')}")
    probed = True if args.no_probe else probe(llm_settings)
    say("Extras")
    install_extras(pr, args)
    with open(toml, "w", encoding="utf-8") as fh:
        fh.write(render_toml(name, lang, folders, sources, llm_settings, False))
    ok(f"wrote {toml}")
    config.apply(force=True)    # this process reads the new folders from here on
    say("Background runs")
    scheduled = setup_schedule(pr, args, vault, in_protected)
    if scheduled:
        with open(toml, "w", encoding="utf-8") as fh:
            fh.write(render_toml(name, lang, folders, sources, llm_settings, True))
    if probed:
        first_run(pr, args, lang)
    say("Ready")
    pointer = os.path.expanduser("~/.config/brainless/vault")
    try:
        with open(pointer, encoding="utf-8") as fh:
            current = fh.readline().strip()
    except OSError:
        current = ""
    if current and os.path.realpath(current) != os.path.realpath(vault):
        warn(f"the `brainless` command still opens {current}. For this vault, run "
             f"`export BRAINLESS_VAULT={vault}` first, or make it the default with "
             f"`echo {vault} > {pointer}`.")
    print(f"""
  brainless add "a thought"     capture a note (it lands in {folders['daily']}/)
  brainless add <file>          drop a document into {folders['inbox']}/
  brainless tick                run what is due now
  brainless search "<query>"    search what has been compiled
  brainless doctor              check the setup
""")
    return 0 if probed else 1


if __name__ == "__main__":
    raise SystemExit(main())
