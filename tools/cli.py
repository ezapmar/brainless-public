#!/usr/bin/env python3
"""brainless command line: one dispatcher for macOS, Linux and Windows.

    brainless <command> [args]      (brainless help for the list)

The shims (bin/brainless for a POSIX shell, bin/brainless.cmd for Windows) only
find the vault and its Python, then hand over to this file. Every command maps
to one tool in tools/, so the table below is the whole command line.
Vault resolution: $BRAINLESS_VAULT, else ~/.config/brainless/vault, else ~/brainless.
"""
import os
import subprocess
import sys

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# command -> tool (relative to tools/) and the fixed arguments before the user's.
TOOLS = {
    "compile":   ("compile_resources.py", []),
    "search":    ("wiki_search.py", []),
    "lint":      ("lint_wiki.py", []),
    "calibrate": ("calibrate.py", []),
    "digest":    ("nightly_processor.py", []),
    "closeout":  ("evening_closeout.py", []),
    "dashboard": ("build_dashboard.py", []),
    "today":     ("today_queue.py", []),
    "file":      ("file_query.py", []),
    "export":    ("export_public.py", []),
    "config":    ("config.py", []),
    "init":      ("init_wizard.py", []),
    "doctor":    ("doctor.py", []),
    "tick":      ("jobqueue.py", ["tick"]),
    "queue":     ("jobqueue.py", ["queue"]),
    "add":       ("jobqueue.py", ["add"]),
    "schedule":  ("schedule.py", []),
    "profile":   ("profiles.py", []),
    "media":     ("media_import.py", []),
    "backup":    ("vault_archive.py", []),
    "graph":     ("graph_export.py", []),
    "eval":      ("retrieval_eval.py", []),
    "chats":     ("chat_import.py", []),
}
# The lite profile hides the commands that assume the full deployment (profiles.py).
GATED = {"closeout", "dashboard", "today", "media", "backup", "graph", "eval", "chats", "health", "export"}

HELP = """brainless <command> [args]

  compile [--dry-run|--full-rebuild|--only <phase>]   build .wiki/ from your notes
  search "<query>" [--k N] [--json]                    search the compiled wiki
  run <script.py> [args]                               run any engine script with the import path set
  test [-v] [-p pattern]                               run the unit tests
  dialectic "<thesis>" [--run noon|evening|night] [--buzz] [--parallel]
                                                        six personas argue it (local by default; --buzz uses the live agents)
  dialectic --scorecard                                 print the rolling 30 day dialectic scorecard
  lint [--fix] [--fix-links] [--dry-run]               wiki integrity checks and repairs
  calibrate                                            decisions due for grading, missing predictions
  health                                               refresh and print _Agent-Context/HEALTH.md
  digest                                               nightly digest of Thinking/Daily (archives captures)
  closeout [--dry-run]                                 evening close-out appended to today's briefing
  dashboard                                            rebuild the projects dashboard
  today [--build|--send]                               preview, save, or send the three-item Today queue
  file <command> "<title>" < note.md                   file a result into .wiki/digests/queries/
  media add <url> | run | list                          YouTube and podcast links in as transcripts
  backup create | verify --identity <key> | status     monthly encrypted archive and its restore test
  graph [--no-summaries] [--main-only]                 the link graph as a Gephi file (logs/graph/)
  eval [--min-hit5 N]                                  ask the golden questions, score the search
  chats triage|import|promote <file>                   bring Claude/ChatGPT history in, filtered
  export --out <dir> [--update]                        produce the public engine tree from this vault
  config [show] | config secret set|check|delete <name>  settings from brainless.toml; keys in the OS keychain
  init [--yes ...]                                     set up a lite vault here: folders, model, scheduler
  doctor [--probe]                                     check the lite setup, one line per check
  add "<thought>" | add - | add <file>                 capture a note, or copy a document to the inbox
  tick [--dry-run]                                     run what is due (the scheduler calls this)
  queue [list|add <kind>|retry|clear]                  the job queue: convert, digest, compile, lint, prune
  schedule [show|install|uninstall|status]             the one scheduler entry of the lite profile
  concepts [list|review|decide <slug> yes|no]          decide concept proposals here instead of in Buzz
  profile [enable|disable <cmd>]                       lite or full; turn a hidden full-profile command on
  update                                               git pull and refresh dependencies
  vault                                                print the vault path
  version                                              print the engine version"""


def resolve_vault():
    env = os.environ.get("BRAINLESS_VAULT")
    if env:
        return os.path.expanduser(env)
    pointer = os.path.join(os.path.expanduser("~"), ".config", "brainless", "vault")
    try:
        with open(pointer, encoding="utf-8-sig") as fh:
            first = fh.readline().strip()
        if first:
            return os.path.expanduser(first)
    except OSError:
        pass
    return os.path.join(os.path.expanduser("~"), "brainless")


def _py(script, args):
    return [sys.executable, os.path.join(ENGINE, "tools", script), *args]


def _python_path(vault):
    """tools/ and .agents/scripts/, then whatever PYTHONPATH already held."""
    dirs = [os.path.join(vault, "tools"), os.path.join(vault, ".agents", "scripts")]
    if os.environ.get("PYTHONPATH"):
        dirs.append(os.environ["PYTHONPATH"])
    return os.pathsep.join(dict.fromkeys(dirs))


def _run(argv):
    return subprocess.run(argv).returncode


def dialectic_argv(args):
    """`brainless dialectic "<thesis>"` means --topic; --local unless --buzz."""
    args = list(args)
    if args and not args[0].startswith("-"):
        args = ["--topic", args[0], *args[1:]]
    if "--buzz" in args:
        args.remove("--buzz")
    else:
        args.insert(0, "--local")
    return _py("dialectic.py", args)


def concepts_argv(args):
    sub, rest = (args[0], args[1:]) if args else ("list", [])
    if sub == "review":
        return _py("concept_review.py", ["--review"])
    if sub == "decide":
        return _py("concept_review.py", ["--decide", *rest])
    return _py("concept_review.py", ["--list"])


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd, args = (argv[0], argv[1:]) if argv else ("help", [])
    if cmd in ("help", "-h", "--help"):
        from brand import paint
        print(paint("brainless", "vermilion", bold=True) + HELP[len("brainless"):])
        return 0

    vault = resolve_vault()
    if not os.path.isdir(os.path.join(vault, "tools")):
        print(f"brainless: no vault at {vault} (set BRAINLESS_VAULT or run the installer)", file=sys.stderr)
        return 1
    os.environ["BRAINLESS_VAULT"] = vault
    # The tools import each other by bare module name across tools/ and
    # .agents/scripts/; this is the one place that puts both on the path.
    os.environ["PYTHONPATH"] = _python_path(vault)
    if sys.platform.startswith("win"):
        os.environ.setdefault("PYTHONUTF8", "1")   # notes are UTF-8; the Windows default is not
    os.chdir(vault)
    if cmd == "run":
        # Any engine script, with the path set: brainless run .agents/scripts/crm_capture.py
        if not args:
            print("brainless run <script.py> [args]", file=sys.stderr)
            return 2
        return _run([sys.executable, os.path.join(vault, args[0]), *args[1:]])
    if cmd == "test":
        return _run([sys.executable, "-B", "-m", "unittest", "discover", "-s",
                     os.path.join(vault, "tools", "tests"), *args])

    if cmd in GATED and _run(_py("profiles.py", ["check", cmd])) != 0:
        return 3
    if cmd in TOOLS:
        script, fixed = TOOLS[cmd]
        return _run(_py(script, [*fixed, *args]))
    if cmd == "dialectic":
        return _run(dialectic_argv(args))
    if cmd == "concepts":
        return _run(concepts_argv(args))
    if cmd == "health":
        rc = _run(_py("health_check.py", args))
        if rc == 0:
            with open(os.path.join(vault, "_Agent-Context", "HEALTH.md"), encoding="utf-8") as fh:
                print(fh.read(), end="")
        return rc
    if cmd == "update":
        rc = _run(["git", "pull", "--rebase", "--autostash"])
        if rc == 0:
            rc = _run([sys.executable, "-m", "pip", "install", "--quiet", "-r", "requirements-core.txt"])
        if rc == 0:
            print("updated")
        return rc
    if cmd == "vault":
        print(vault)
        return 0
    if cmd in ("version", "--version"):
        try:
            with open(os.path.join(vault, "VERSION"), encoding="utf-8") as fh:
                print(fh.read().strip())
        except OSError:
            print("unknown")
        return 0
    print(f"brainless: unknown command '{cmd}' (try: brainless help)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
