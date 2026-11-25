<p align="center">
  <img src="docs/assets/brainless-banner.png" alt="brainless" width="760">
</p>

<p align="center">
  <b>Notes arrive from everywhere, collect in one folder, link themselves, and argue back when you are about to decide.</b>
</p>

<p align="center">
  <a href="https://github.com/ezapmar/brainless-public/releases"><img alt="release" src="https://img.shields.io/github/v/release/ezapmar/brainless-public?style=flat-square&labelColor=0A1825&color=F24B1E&label=release"></a>
  <a href="LICENSE"><img alt="licence" src="https://img.shields.io/badge/licence-MIT-0C9794?style=flat-square&labelColor=0A1825"></a>
  <img alt="python" src="https://img.shields.io/badge/python-3.13-FBA335?style=flat-square&labelColor=0A1825">
  <img alt="one mac" src="https://img.shields.io/badge/runs-on%20one%20Mac-FBDEA5?style=flat-square&labelColor=0A1825">
</p>

<p align="center">
  <a href="https://ezapmar.github.io/brainless-public/">Site</a> &nbsp;·&nbsp;
  <a href="https://github.com/ezapmar/brainless-lite">Lite edition</a> &nbsp;·&nbsp;
  <a href="#how-it-runs">How it runs</a> &nbsp;·&nbsp;
  <a href="#install">Install</a> &nbsp;·&nbsp;
  <a href="docs/ARCHITECTURE.md">Architecture</a> &nbsp;·&nbsp;
  <a href="docs/design.md">Design</a>
</p>

It runs on your own Mac. A folder of plain Markdown notes, small Python scripts, and two models wired by job. No account, no hosted database, nothing you could not open in a text editor twenty years from now.

Sherlock had the method. Watson wrote it down, asked the obvious question, and kept the files. This is the Watson. You keep the method.

> **Want the same thinking loop with zero background setup?** Try [brainless-lite](https://github.com/ezapmar/brainless-lite) ([site](https://ezapmar.github.io/brainless-lite/)). No Telegram, no `launchd`, no local model server required. Works on macOS, Linux and Windows with one CLI and any coding agent.

---

## Why I built it

My notes used to live in six places. Voice memos in the car, whiteboard photos, meeting transcripts in e-mail, saved links, midnight lines in a chat window. Writing something down and finding it six months later turned out to be two completely different things.

The bigger problem was decisions. I run a company, so most days are a string of judgement calls made between meetings and never checked again. That is how you stay wrong in the same way twice.

A folder of notes does not fix that. Unread notes are storage. Summaries nobody challenges are tidier storage. And an argument nobody grades is entertainment, and I already have football for that.

## The loop

Four steps, each depending on the one before it.

1. **Collect without judging.** Voice notes, photos, links, PDFs, e-mails labelled `Brain`, and quick lines go into one folder through Telegram or Obsidian Web Clipper. Nothing is tagged or filed on the way in, because filing at capture time is how people stop capturing. Voice transcribes locally with `whisper.cpp`.
2. **Compile and connect.** Every night at 23:00 (or immediately with `/ingest`), the compiler summarises new sources into `.wiki/`, links people and recurring concepts, keeps disagreements side by side with dates, and weights observed post-ship outcomes above untested plans. Both `.wiki/` (`lint_wiki.py`) and the Obsidian-visible vault (`vault_graph.py`) are checked for orphans, broken links, dead-ends, and accidental links into gitignored folders.
3. **Argue before you commit.** Before a hard choice, `/decide` pulls your stated beliefs and past decisions, asks what undoing the choice costs, and forces criteria before options. Overnight, new sources are checked against nearby pages for contradictions, and `attention_audit.py` compares what you say matters against the folders you actually touched this week.
4. **Bet, grade, follow through.** Beliefs carry a "what would change my mind" line. Decisions carry a prediction, confidence, and review date. When the date passes, the system nags until you grade what happened. Promises from notes and meetings sync with Google Tasks, and a model-free morning briefing pulls health, kill criteria, promises, wiki changes, and attention gaps into one file after 07:00.

The moment that paid for the whole system happened on 9 September 2026. The first overnight run caught that my argument against a partner's equity ask was aimed at the wrong point. Walking into that meeting confidently wrong would have been expensive.

---

## How it runs

One Mac, one folder, four `launchd` jobs, one Telegram chat. Since 15 November 2026 there is no Linux worker, no chat relay, and no VPN. I had three devices and two schedulers, and the plumbing had become the hobby.

```
iPhone / iPad ──Telegram──▶ Mac  capture (every 2 min)
                                  ├─ plain message -> note in Thinking/Daily or Inbox/Links
                                  ├─ reply to bot  -> approve, defer, follow up
                                  └─ "?question"   -> cited answer on local model
iPhone / iPad ◀─iCloud────▶ Mac  the vault, opened in Obsidian
                            Mac  hourly: tasks, mail (Spiky + Gmail "Brain"),
                                 meeting brief, model-free morning briefing after 07:00, health
                            Mac  23:00: ingest, digest, compile, index, links, lint,
                                 vault graph, attention audit, Sunday weeklies, one commit + push
                            Mac  always: local Gemma 4 12B on 127.0.0.1:8080
GitHub (private)                 off-site copy only, one push a night
```

| Job | Schedule | Script | Commits to git? |
|---|---|---|---|
| `capture` | every 2 minutes | `.agents/scripts/capture.sh` | no |
| `hourly` | every hour | `.agents/scripts/cron_wrapper.sh` | no |
| `night` | 23:00, or on wake | `.agents/scripts/night.sh` | yes, once |
| `llm` | always on | `llama-server` | no |

- **One writer.** Only `night.sh` commits and pushes, so concurrent jobs never collide. The push refuses any remote that is public or cannot be verified private.
- **One channel.** `tools/notify.py` backs the Telegram bot with a SQLite outbox (queued, retried, sent at most once per key). Replies route by channel label (`#tasks`, `#thinking`, `#dreaming`, `#ops`).
- **Two models, fail-closed routing.** `brainless.toml` is the single routing file. Eight short or private lanes (`note-classify`, `dreaming-judge`, `compile-aliases`, `compile-local`, `capture-note`, `capture-photo`, `spiky-actions`, `ask`) run locally on Gemma 4 12B and are pinned in `local_only`. If the local server is down or a stray override points one at the cloud, `tools/llm.py` refuses the call and the text stays on the Mac. Heavy writing lanes (`compile`, digests, weekly synthesis) use Claude CLI on a subscription.

Full operational map: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Keep it yours

A script running against your life needs hard checks, not polite prompts.

- **Human vs machine areas.** You own `Work/`, `Personal/`, `Library/`, `Thinking/`, `Inbox/`, `Writings/`. Agents ask before writing there. `raw/` is strictly read-only, denied in `.claude/settings.json` and `tools/hooks/claude_guard.py`. The engine owns `.wiki/` and can regenerate it from scratch.
- **Three layers before any cloud call.** `tools/redact.py` masks e-mails, phones, IBANs, IDs, and keys. `tools/cloud_gate.py` enforces `cloud_exclude` folders in `PROFILE.md` plus note-level `privacy: personal` (compiled locally on `compile-local`) and `privacy: secret` (skipped entirely). `.agents/state/egress.jsonl` logs every call with hashed source paths, and `health_check.py` turns red on drift.
- **Visible graph leak guard.** `tools/vault_graph.py` turns red if a git-tracked note links directly to a gitignored file, and flags unexpected `Work` and `Personal` bridges outside `cross_domain_allow` in `PROFILE.md`.
- **Shared memory across agents.** Standing rules and past corrections live in `_Agent-Context/LEARNINGS.md`, so interactive chats, nightly runs, and Telegram replies read the same list.
- **Encrypted off-sync backup.** Every Sunday, `tools/vault_archive.py` seals the full vault with `age` outside the synced folder. Sync copies a bad night everywhere in minutes; an encrypted archive does not.

---

## Install

Requires macOS, `git`, Homebrew, Python 3.13, a Telegram bot, and the Claude CLI.

```bash
git clone https://github.com/ezapmar/brainless-public.git ~/projects/brainless
cd ~/projects/brainless
python3.13 -m venv ~/.local/venvs/brainless
~/.local/venvs/brainless/bin/pip install -r requirements-core.txt -r requirements-search.txt -r requirements-google.txt
```

Vault path defaults to `~/projects/brainless` (override with `BRAINLESS_VAULT`). Then:

1. **Profile.** Edit `_Agent-Context/PROFILE.md`: name, output language (`en` or `tr`), `protected_homes`, `cloud_exclude`, and optional `cross_domain_allow`. Add health and identity folders before your first commit.
2. **Telegram bot.** Create one with BotFather. Store the token in `~/.config/brainless/telegram_token` and your chat id in `~/.config/brainless/telegram_chat_id`. Secrets never enter the vault.
3. **Models.** Sign in to the Claude CLI. For local lanes, run `brew install llama.cpp`, place a Gemma 4 12B GGUF and its vision projector in `~/models/gemma-4-12b/`, and copy `brainless.toml.example` to `brainless.toml`. Check routing any time with `python3 tools/llm.py --lanes`.
4. **Launchd jobs.**
   ```bash
   bash .agents/launchd/install.sh
   ```
   Rerun after moving paths; pass `--uninstall` to remove them.

**Optional inputs.** Documents work out of the box via `markitdown`. Voice needs `ffmpeg` and `whisper.cpp`. Backups need `age`. Google Tasks and meeting briefs need your own Google OAuth client. E-mail intake reads a Gmail `Brain` label over read-only IMAP. Browser clipping templates live in [docs/clipper-templates/](docs/clipper-templates/).

**MCP server (optional).** Expose `.wiki/` read-only to any MCP client (skips gitignored files):

```bash
claude mcp add --scope user brainless-wiki -- ~/.local/venvs/brainless/bin/python ~/projects/brainless/tools/mcp_server.py
```

## First ten minutes

Send your bot a sentence; two minutes later it lands in `Thinking/Daily/`. Send `?what do I know about pricing` for a cited answer from your own pages.

Run a compile and search without waiting for 23:00:

```bash
source .agents/scripts/env.sh
python3 tools/compile_resources.py --file Thinking/Daily/today.md
python3 tools/wiki_search.py "the question you keep revisiting"
python3 tools/vault_graph.py --no-write
```

Copy `_Templates/Belief.md` into `Thinking/Beliefs/` and `_Templates/Decision.md` into `Thinking/Decisions/` so the checks have real claims to push against.

Inside Claude Code (or any agent that reads `.wiki/_commands/`), eight slash commands and two subagents (`scout` and `librarian` in `.claude/agents/`) live next to your notes:

| Command | What it does |
|---|---|
| `/ingest <path>` | Compile one source immediately (15-page cap), check conflicts, return a 3-sentence diff |
| `/decide <question>` | Weigh a decision against your beliefs and past bets, reversibility first |
| `/research <question>` | Opening question and falsifiable hypotheses before opening sources |
| `/trace <project>` | Trace an active project across sources, `.wiki/`, tasks, conflicts and attention |
| `/reply <thread>` | Draft a commitment-checked reply into `Writings/Drafts/` |
| `/weekly` | Weekly themes, blind spots, and promotion candidates |
| `/context` | Current state snapshot and `CONTEXT.md` drift check |
| `/lint` | Wiki integrity checks (broken links, orphans, dead-ends) and repairs |

Every answer files back into `.wiki/digests/queries/`, so tomorrow's search builds on today's reasoning.

---

## Full vs Lite

| | **brainless** (this repo) | **[brainless-lite](https://github.com/ezapmar/brainless-lite)** |
|---|---|---|
| Platform | macOS (`launchd`) | macOS, Linux, Windows |
| Background jobs | 4 `launchd` jobs, nightly auto-commit | None, run on demand |
| Capture | Telegram bot, Gmail `Brain`, Spiky, Clipper | Plain files, Clipper, `brainless add` |
| Models | Claude CLI + local Gemma 4 12B (`llama-server`) | Any coding agent or LLM in `brainless.toml` |
| Best for | Always-on personal OS on a Mac | Trying the method in 5 minutes with zero ops |

## Honest limits

- This repo assumes macOS, a Claude subscription for heavy writing, and enough RAM for a local 12B model. If you want Linux, Windows, or no background services, start with [brainless-lite](https://github.com/ezapmar/brainless-lite).
- English and Turkish ship out of the box (`tools/locale/`). Other languages need a new label folder.
- Some integrations (CRM snapshot, Spiky, Google Tasks) are mine; they skip quietly when unconfigured.
- The name started as a joke in the office ("let us use our own brain first, then talk about a second brain"). It stuck because it states the rule: the scripts keep the memory and the edges, the thinking stays yours.

## Learn more and tests

- [Architecture](docs/ARCHITECTURE.md) · [Method](docs/method.md) · [Thinking Clearer](docs/thinking-clearer.md) · [Local Inference](docs/local-inference.md) · [References](docs/references.md) · [Design](docs/design.md)
- Site: [ezapmar.github.io/brainless-public](https://ezapmar.github.io/brainless-public/) · [What's new](https://ezapmar.github.io/brainless-public/whats-new.html)

Offline regression tests (no network, no credentials, no model calls):

```bash
python3 tools/run_tests.py
```

MIT licence. See `LICENSE`.

<p align="center">
  <img src="docs/assets/brainless-logo-64.png" alt="" width="40"><br>
  <sub>Capture everything. Compile at night. Grade yourself in the morning.</sub>
</p>
