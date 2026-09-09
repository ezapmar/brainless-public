# brainless

A second brain that argues back.

brainless is a plain folder of Markdown notes (an Obsidian vault works well) plus a
set of small scripts that turn your captures into a compiled, searchable knowledge
layer, keep a ledger of your beliefs and decisions, grade those decisions later, and
put your daily thinking in front of five critical-thinking personas that try to break
it. You own the notes. The machine owns the compiled layer and can regenerate it at
any time.

The name is the point: offload memory and synthesis so your head stays free.

## What it does

- **Capture** anything into `Inbox/` or `Thinking/Daily/`. Documents (PDF, DOCX,
  XLSX, PPTX, images) are converted to Markdown on a schedule.
- **Compile** the human-owned homes into `.wiki/`: one summary per source, concept
  articles, project status mirrors, a daily digest, an index and a lint report.
- **Think** with slash commands that read your beliefs and decisions: `/decide`,
  `/contradict`, `/ideas`, `/weekly`, `/trace`, `/connect`, `/graduate`, `/context`.
- **Argue** with `/dialectic`: five personas grounded in five books test a thesis in
  two rounds and a moderator writes the synthesis, the bet, and the proposed test.
  - Skeptic (Browne & Keeley, *Asking the Right Questions*)
  - Gambler (Annie Duke, *Thinking in Bets*)
  - Scientist (Camuffo et al. 2024, the scientific approach to entrepreneurial decisions)
  - Postmortem (Amy Edmondson, *Right Kind of Wrong*)
  - Strategist (Lafley & Martin, *Playing to Win*)
- **Calibrate**: every decision note carries a prediction, a confidence and a review
  date; `tools/calibrate.py` tells you which ones are due for grading.

Nothing writes into your notes without you. Every command and job proposes; you paste.

## Layout

| Folder | Owner | Purpose |
|---|---|---|
| `Work/`, `Personal/`, `Library/` | you | projects, life, reference material |
| `Inbox/` | you | unsorted capture, triaged later |
| `Thinking/` | you | `Daily/`, `Ideas/`, `Beliefs/`, `Decisions/`, calibration |
| `.wiki/` | the machine | compiled layer, regenerable, never hand-edited |
| `_Agent-Context/` | shared | `PROFILE.md`, context, tasks, health, conventions |
| `_Templates/` | shared | note templates for ideas, beliefs, decisions, people |
| `tools/` | engine | compiler, lint, search, filer, calibration, dialectic |
| `.agents/` | engine | scheduled jobs, systemd units, persona definitions |

A project folder is any folder under `Work/` or `Personal/` with a `notes.md`.

## Quick start

Requirements: Python 3.11+, git, and one LLM backend. The default backend is the
`claude` CLI signed in with a subscription. Any OpenAI-compatible endpoint works
instead (OpenAI, Together, Grok, Ollama, LM Studio).

```bash
git clone <this repo> ~/projects/brainless
cd ~/projects/brainless
bash setup.sh                      # checks python, markitdown, the LLM backend
cp .env.example ~/.config/brainless.env   # optional; edit what you need
```

Then edit `_Agent-Context/PROFILE.md`: your name, output language (`en` or `tr`),
the folder under `Work/` that is your company, and any extra private folder names.

Try it:

```bash
python3 tools/compile_resources.py --dry-run     # what would be compiled
python3 tools/compile_resources.py               # build .wiki/
python3 tools/wiki_search.py "some topic" --k 5
python3 tools/dialectic.py --local --topic "We should ship the feature this quarter"
```

Inside Claude Code (or Gemini CLI) from the vault root, the slash commands in
`.claude/commands/` are available. Their canonical definitions live in
`.wiki/_commands/` and apply to any agent that can read files.

## Running it on a schedule

Everything scheduled is a small script under `tools/` or `.agents/scripts/`. Two ways
to run them:

- **One machine, macOS**: launchd plists (`setup.sh` prints the labels; they use the
  prefix `BRAINLESS_LABEL_PREFIX`, default `com.<user>`). Hourly conversion, nightly
  digest and compile, weekly lint.
- **An always-on worker (Linux)**: the units in `.agents/systemd/` install with
  `bash .agents/systemd/install.sh`. They wrap each job in `worker_job.sh`, which
  pulls, runs, commits and pushes, so a second machine can be the primary author.
  Set `worker_name` in `PROFILE.md`; commits from the worker carry that suffix and
  the watchdog attributes by it.

Two writers on one repo follow `_Agent-Context/TRUNK-BASED-DEVELOPMENT.md`: linear
history, fast-forward pushes, one owner per file, append-only shared files.

## Addons (optional, documented, not required)

- **Telegram capture**: voice notes, photos, links and text become `Thinking/Daily/`
  notes; transcription runs locally with whisper.cpp. `.agents/scripts/telegram_capture.py`.
- **Buzz personas**: the five dialectic personas as live agents on a self-hosted
  [Buzz](https://github.com/block/buzz) relay, answering in a channel twice a day
  and whenever you mention them. `.agents/buzz/` holds the prompts and the installer.
- **Google Tasks and Calendar**: two-way task sync and a pre-meeting brief.
- **Meeting transcripts**: an IMAP ingest for meeting report e-mails.

Each addon needs its own credentials in `~/.config/brainless/`. None of them are
needed for the core loop.

## Privacy model

- The repo you clone contains no notes. Content homes are gitignored in the public
  engine repo; your vault lives wherever `BRAINLESS_VAULT` points.
- Generic privacy rules ignore identity papers, health folders and official
  documents anywhere in the tree. Add your own folder names to `private_segments`
  in `PROFILE.md` and they will never be compiled into `.wiki/`.
- LLM calls never get file or shell tools. Context is embedded in the prompt.
- `tools/export_public.py` is how this repo is produced from a private vault: a
  whitelist copy plus a leak scan that refuses to pass on identity numbers, keys,
  tokens or the owner's name.

## Design notes

- Humans own the source; the machine owns a regenerable layer. Never edit `.wiki/`.
- Every analysis is filed back into `.wiki/digests/queries/` so later questions can
  build on earlier answers.
- Decisions are bets. Grade them, or judgment cannot compound.
- Personas never mention each other and only answer the moderator and the owner, so
  agents cannot trigger each other.

## License

MIT. See `LICENSE`.
