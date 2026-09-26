# Lite

The lite profile is brainless on one computer, with a folder of notes and whichever
model you already use. No second machine, no phone bot, no Obsidian, no always-on
anything. It is the default when you install.

## What you get

- **A folder.** Your notes live in plain Markdown under the folder you install into.
  The folder names are in your language: `Inbox`, `Notes`, `Library`, `Thinking` in
  English; `Gelen`, `Notlar`, `Kütüphane`, `Düşünce` in Turkish. Obsidian can open the
  folder as a vault. So can any editor.
- **Any model.** Ollama on your own machine, a cloud API with a key (Claude, OpenAI,
  Grok, Gemini, OpenRouter), or a command-line tool you are already signed in to
  (Claude Code, Codex, Gemini CLI).
- **One background entry.** Every 15 minutes, and at login, `brainless tick` looks at
  what is due and runs it. The digest and the compile run each evening; a night the
  laptop slept through runs at the next wake.
- **The same compile, search and thinking tools** as the full setup: the wiki,
  `brainless search`, the six-voice dialectic, calibration, the slash commands.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/ezapmar/brainless-public/main/install.sh | bash
```

Requirements: git and Python 3.11 or newer (on a Mac, `brew install python`). The script
sets up Python and the `brainless` command, then `brainless init` asks, one question at
a time:

1. Your first name and the language brainless writes in.
2. The folder names, in that language. Rename any of them if you like.
3. Other folders to read, such as an old Obsidian vault. They are linked under
   `Library/_linked/`, read for their `.md` files, and never written to.
4. The model. For Ollama it offers to install it and download a model; for an API it
   asks for the key and keeps it in the macOS Keychain (libsecret on Linux), never in a
   file in the vault; for a CLI it checks that it is installed. Then one test call.
5. Semantic search, optional.
6. Background runs, and on a Mac the one permission they may need (see below).
7. A first note and a first run, if you want to see it work.

Every answer lands in `brainless.toml` at the vault root. Edit it by hand any time;
`brainless config show` prints what it sets. Re-running `brainless init` keeps it unless
you pass `--force`.

Unattended, for a script or a CI job:

```bash
bash install.sh --vault ~/brainless --yes -- --lang en --provider ollama --model qwen3:8b
bash install.sh --yes -- --provider anthropic --api-key-env ANTHROPIC_API_KEY --no-schedule
```

`bash install.sh --profile full` installs the multi-machine setup instead, as in
[Reference Deployment](reference-deployment.md).

## Every day

```bash
brainless add "a thought, as it comes"     # a note in your daily folder
brainless add ~/Downloads/report.pdf       # a document into the inbox
brainless search "that thing about teams"
brainless dialectic "We should hire before we have the revenue"
```

The evening digest reads the day's notes; the compile turns notes and documents into the
wiki under `.wiki/`. Both run by themselves if background runs are on. To run what is
due right now: `brainless tick`.

## Keeping an eye on it

```bash
brainless doctor            # one line per check: folders, model, key, scheduler, jobs
brainless doctor --probe    # the same, plus one tiny call to the model
brainless queue             # what ran, what waits, what failed and why
brainless queue retry       # put failed jobs back
```

A job that needs the network or the model waits while either is missing: on a train
the lint still runs and the compile waits for Wi-Fi. A failed job is retried after 5
minutes, 30 minutes, 2 hours and 6 hours, then stays failed until `brainless queue retry`.

## Concepts

When several notes share an idea that has no page yet, the compile proposes a concept.
In the full setup the proposal arrives in a chat channel; here you decide it in the
terminal:

```bash
brainless concepts            # what is waiting
brainless concepts review     # yes, no or skip, one by one
```

At most five proposals wait at a time, so reviewing them now and then keeps new ones
coming.

## Commands

`brainless help` lists everything. The lite profile hides the commands that assume the
full setup (close-out, Today queue, dashboard, health, media, backup, graph, eval,
chats, export). `brainless profile` shows them; `brainless profile enable media` turns one
on.

## Models

| You choose | What it needs | Notes |
|---|---|---|
| `ollama` | Ollama running on this machine | Nothing leaves the machine. Slower; a 7 to 8 billion parameter model is a fair start. |
| `anthropic` | an API key | Default model `claude-opus-5`. |
| `openai`, `grok`, `gemini`, `openrouter` | an API key | Pick the model when asked; `python3 tools/llm.py --models <provider>` lists them. |
| `claude-cli` | Claude Code, signed in | Uses your subscription. The only option that can search the web for the research lane, and read photos. |
| `codex-cli`, `gemini-cli` | that CLI, signed in | Runs from an empty folder, Codex in its read-only sandbox. These are agents, a weaker boundary than the others: keep them away from lanes that read untrusted text if you can. |

Each provider reads only its own key. Keys are set with
`brainless config secret set <name>` (`anthropic_api_key`, `openai_api_key`,
`xai_api_key`, `gemini_api_key`, `openrouter_api_key`). A lane can use its own provider
and model: see [Local Inference](local-inference.md) and `[llm.lanes.<lane>]` in
`brainless.toml.example`.

## On a Mac

The background entry is a launch agent, `~/Library/LaunchAgents/com.brainless.lite.plist`.
If the vault sits inside `~/Documents`, `~/Desktop` or iCloud Drive, macOS asks every
background job for permission to read it; `brainless init` warns about that and can open
the Full Disk Access settings. `~/brainless` avoids the question.

## Remove it

```bash
brainless schedule uninstall
rm -rf ~/.local/bin/brainless ~/.config/brainless
```

That stops it and removes the command. Your notes are still in `~/brainless`; delete
that folder too only if you do not want them. Keys stay in the Keychain under the service `brainless` until you delete them there, or
with `brainless config secret delete <name>` first.

## Honest limits

- Photo OCR needs `claude-cli`. On any other model photos stay in the inbox untouched.
- Web research needs `claude-cli`; without it that lane is skipped.
- A small local model writes thinner summaries than a large cloud one.
- Windows is not supported yet.
