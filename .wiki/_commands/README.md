# Commands — the Intelligence Layer

This folder is the **canonical contract** for every slash command your AI agents can run against the vault. Claude Code and Gemini CLI wrappers point back here, so editing a file in `.wiki/_commands/` changes the behavior of both agents at once.

## What lives where

```
.wiki/_commands/              ← canonical prompts (source of truth)
  README.md             ← this file
  _shared-rules.md      ← guardrails every command must follow
  context.md
  trace.md
  connect.md
  ideas.md
  graduate.md
  decide.md
  weekly.md
  contradict.md

.claude/commands/       ← Claude Code wrappers — invoke via `/context` etc.
  context.md
  trace.md
  …

.gemini/commands/       ← (optional) Gemini CLI equivalents, TOML format
```

**Golden rule:** edit `.wiki/_commands/<name>.md` to change what a command does. The wrappers are dumb — they only tell the agent to load the canonical file.

## How to use a command

From the vault root, launch Claude Code (or Gemini CLI) and type:

```
/context
/trace ticket-system
/connect "Kolay Ticket" "Emergency Helper"
/ideas health
/graduate 30
/decide "Should I lease or buy in London?"
/weekly
/contradict
```

Each command reads specific parts of the vault, reasons over them, and prints a result. **No command writes to the vault** unless you explicitly confirm the proposed change.

## The 8 commands

| Command | What it does | Reads from |
|---|---|---|
| `/context` | Snapshot of your current state — projects, priorities, staleness | `_Agent-Context/`, `.wiki/digests/` (last 7) |
| `/trace <topic>` | Timeline of how your thinking on a topic evolved | whole vault, grep + dates |
| `/connect <A> <B>` | Shortest link-path between two notes, plus proposed new edges | `[[links]]` across vault |
| `/ideas [area]` | Generate atomic idea candidates from belief × project intersections | `Thinking/Beliefs/`, `.wiki/ideas/`, recent dailies |
| `/graduate [days]` | Seeds ready for promotion to `#status/growing` or `#status/evergreen` | notes tagged `#status/seed` |
| `/decide <question>` | Fills in a decision framework using your beliefs + past decisions | `Thinking/Beliefs/`, `Thinking/Decisions/` |
| `/weekly [days]` | Themes, blind spots, promotion candidates from recent dailies | `.wiki/digests/`, git log |
| `/contradict` | Pairs of beliefs/decisions that conflict; stale unused beliefs | `Thinking/Beliefs/`, `Thinking/Decisions/` |

## Install checklist

- [ ] Claude Code wrappers exist in `.claude/commands/` (already created — one `.md` per command).
- [ ] Restart Claude Code so it picks up the new commands. `/` autocomplete should list all 8.
- [ ] (Optional) Mirror to Gemini CLI — see **Gemini parity** below.
- [ ] Run `/context` as a smoke test — it's the lowest-risk command and validates that agents can read `_Agent-Context/`.

## First-use order (recommended)

Run these in order on your first day with commands installed. Each one reveals gaps that the next one helps fix:

1. **`/context`** — confirms agents see your current state, flags drift between CONTEXT.md and recent dailies.
2. **`/weekly`** — surfaces what you've actually been doing. Probably proposes updates to CONTEXT.md.
3. **`/graduate 14`** — shows you which seeds (if any) are mature. Will likely return empty for now — that's a signal to seed `.wiki/ideas/` and `Thinking/Beliefs/`.
4. **`/contradict`** — validates beliefs are self-consistent.
5. **`/ideas`** — run this *after* you have ≥5 beliefs and ≥5 ideas written, otherwise output is generic.

The rest (`/trace`, `/connect`, `/decide`) are on-demand — use when the specific question comes up.

## Customizing a command

Every canonical file follows the same shape:

```markdown
---
name: <command-name>
description: <one line>
argument-hint: <what args look like, or "(none)">
---

## Inputs
<what the user provides>

## Reads
<which files/folders the agent must load>

## Behavior
<step-by-step instruction to the agent>

## Output format
<exact shape of the reply>

## Guardrails
Follow `.wiki/_commands/_shared-rules.md`.
```

To change a command:
1. Edit the canonical file in `.wiki/_commands/`.
2. Nothing else needs to change — wrappers re-read it each invocation.
3. Commit the change. `.wiki/_commands/` is meant to be version-controlled so you can see how your own agent contracts evolve.

## Gemini parity

Gemini CLI wrappers are in `.gemini/commands/` as `.toml` files (Gemini uses TOML, not Markdown). They are already created — one per command.

Key differences from Claude Code:

| | Claude Code | Gemini CLI |
|---|---|---|
| File format | `.md` with YAML frontmatter | `.toml` |
| Arguments | `$ARGUMENTS` | `{{args}}` |
| File injection | reads files via tool calls | `@{path/to/file}` inlined at runtime |
| Reload | restart required | `/commands reload` — instant |
| Shell injection | not built-in | `!{shell command}` inside prompt |

The Gemini TOML files use `@{.wiki/_commands/<name>.md}` to inject the canonical prompt directly — no copy-paste drift. Same source of truth, different wrapper syntax.

To reload after editing a Gemini command (no restart needed):
```
/commands reload
```

**Gemini-specific advantage:** use `/weekly` or `/trace` in Gemini when you want Google Drive or Google Calendar context pulled in alongside the vault (Gemini's MCP servers are already configured in `.gemini/settings.json`). Claude is better for deep reasoning; Gemini is better when the answer lives in your Google Workspace.

## Troubleshooting

- **Command doesn't autocomplete** → restart Claude Code; confirm the wrapper file exists in `.claude/commands/`.
- **Agent ignores vault files** → check it's running from the vault root (the directory that holds `_Agent-Context/`).
- **Output cites notes that don't exist** → the canonical file needs stricter "cite only files you actually read" wording in its Behavior section.
- **Agent wants to write to vault** → make sure `_shared-rules.md` is being loaded; re-state the no-write rule at the bottom of the specific command.

## Future commands (not built yet)

Candidates to add once the core 8 are stable:

- `/inbox` — process `Inbox/` into proper folders with suggested links
- `/people <team>` — fill in empty person templates from meeting notes
- `/link-orphans` — find notes with 0 `[[links]]` and propose connections
- `/archive-stale` — projects with no log entry in 60+ days

Don't build these until the core 8 feel natural.
