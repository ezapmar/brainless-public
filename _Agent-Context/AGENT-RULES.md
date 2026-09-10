# Agent Operating Rules

## Core Rules (Claude Code)

### Rule 1: Read Before You Speak
Always load `CONTEXT.md` and `PROJECTS-ACTIVE.md` before responding to any vault-related query. Your suggestions should be grounded in the owner's actual context, not generic advice.

### Rule 2: Never Write to the Vault Without Permission
You may suggest edits, draft notes, and propose links. You do NOT modify vault files unless the owner explicitly instructs you to. The vault is human-authored truth.

### Rule 3: Link, Don't Summarize
When surfacing patterns, point to the specific notes (`[[Note Name]]`) rather than paraphrasing. Let the owner read the originals and form his own connections.

### Rule 4: Respect Note Maturity
- `#status/seed`: raw, unprocessed. Don't treat as reliable context.
- `#status/growing`: partially developed. Good for suggestions, not for decisions.
- `#status/evergreen`: refined and stable. Weight these heavily.

### Rule 5: Surface Contradictions
If you notice beliefs that contradict each other, or decisions that conflict with stated beliefs, flag them. This is one of your highest-value functions.

### Rule 6: Think in the Vault's Language
Use the same tags, link formats, and naming conventions the vault uses. Your output should be ready to paste into Obsidian without reformatting.

### Rule 7: Time-Aware Suggestions
Check dates on notes. A belief from 2 years ago might need challenging. A project with no log entries in 3 weeks might need attention or archiving.

---

## Claude Code Specific

### Strengths to Leverage
- Deep reasoning over complex decision frameworks
- Code generation for automation scripts
- Pattern recognition across many interconnected notes
- Building custom slash commands

### Entry Point
```bash
# From the vault root
claude --context "_Agent-Context/CONTEXT.md"
```

---

## Briefing Convention (single source of truth)

- All morning/daily briefings live in `Daily Briefings/` as `daily-briefing-YYYY-MM-DD.md` (or `.html` for styled versions). One file per day.
- The morning half is produced by the Claude scheduled task `morning-briefing` (weekdays 07:00, `~/.claude/scheduled-tasks/morning-briefing/SKILL.md`; runs only while the desktop app is open). The evening half is appended by `tools/evening_closeout.py` on the worker at 21:00. A manual "brifing" session follows the same convention and must merge into the existing file, never create a second one.
- Never write briefing files to the vault root or invent new name variants (morning-brief, morning-memo, etc. are retired).
- Every briefing must start with a 3-line "Sistem Sağlığı" block sourced from `_Agent-Context/HEALTH.md` (written hourly by `tools/health_check.py`). If HEALTH.md reports a red flag, put it at the top of the briefing.
- If `_Agent-Context/CRM.md` exists (written hourly by `.agents/scripts/crm_capture.py`, read-only pull from the CRM, no LLM), add a "CRM: Enterprise Hattı" section after the open promises: copy its "Bayraklar" and "Değişenler (son 24 saat)" bullets verbatim. Skip the section when both say "yok". A 🔴 CRM status joins the health block at the top. Ad hoc CRM questions and the Business Development screening use the Pipedrive MCP connector interactively, never the briefing. The same snapshot is posted to the Buzz channel `#crm` (identity `crm`, `.agents/scripts/buzz_crm_sync.sh`) whenever its body changes; that channel is the place to discuss accounts and the Business Development line with the assistant.
- If `_Agent-Context/RESURFACE.md` is less than 7 days old, include its 5 notes as a short "Bu Hafta Yeniden Bak" section.
- If `_Agent-Context/CONTEXT-DRIFT.md` reports drift (anything other than "Drift yok"), mention it in the briefing and ask the owner whether to apply the proposed CONTEXT.md updates.
- Read `_Agent-Context/DIALECTIC-STATUS.md` and add one line: "Dün diyalektik: N konu, M/K persona cevabı" with a link to the filed note in `.wiki/digests/queries/` (the evening run, or the noon run if the evening did not happen). If yesterday has no line or the result is `error`, write "Diyalektik turu çalışmadı" in red next to the health block.

---

## Critical Dialectic (Buzz)

- `tools/dialectic.py` runs on the worker at 12:30 and 21:20 (`brainless-dialectic.timer`). It clusters the day's unseen Telegram and Buzz captures into topics, posts each topic in the Buzz channel `#dialectic` as the `moderator` identity, and mentions five live persona agents: Skeptic (Browne & Keeley), Gambler (Duke), Scientist (Camuffo 2024), Postmortem (Edmondson), Strategist (Lafley & Martin). Two rounds (method critique, then rebut the strongest objection), then an LLM synthesis posted in the thread and filed to `.wiki/digests/queries/<date>-dialectic-<slug>.md`. All engine names, prompts and notes are in English (`lang: en`).
- Personas are read-only harnesses (`buzz-persona@<slug>.service`, prompts in `.agents/buzz/personas/`, installer `.agents/buzz/install_personas.sh`). They answer only the moderator and the owner and never mention anyone, so agents cannot trigger each other. The owner can mention any persona in any thread for an ad-hoc answer.
- The engine only proposes (seed, belief, decision stubs). Nothing is written to `Thinking/`; rule 2 holds.
- `/dialectic [topic]` is the local twin of the same rounds (`.wiki/_commands/dialectic.md`).

## Thinking Loop (Telegram)

- `.agents/scripts/thinking_loop.py --ask` runs Sundays 19:00 on the worker (`brainless-thinking.timer`, unit files in `.agents/systemd/`). It sends ONE question by priority: decision due to grade, decision without a prediction, next Thinking Cadence step, stalest belief, then a seed prompt if no idea was captured in 14 days.
- The owner answers by replying to that message (voice or text). `telegram_capture.py` routes the reply to the loop, which drafts the note change and sends a preview. Only "uygula" writes to `Thinking/`; "iptal" drops the draft; "geç" skips the question for the week.
- Every applied answer is also filed to `.wiki/digests/queries/<date>-thinking-<kind>-<slug>.md` so the loopback compounds. The evening close-out and dashboard read the updated notes as usual.

## Slash Command Definitions

Agents should implement these commands when invoked:

| Command | Behavior |
|---------|----------|
| `/context` | Load CONTEXT.md + last 7 daily notes → present current state |
| `/trace <topic>` | Find all notes mentioning topic, sort by date, show evolution |
| `/connect <A> <B>` | Find link paths between two notes/concepts, suggest new connections |
| `/ideas` | Analyze recent notes + beliefs → generate atomic ideas at intersections, each with a stable `zk:` slip-id |
| `/graduate` | Find `#status/seed` notes older than 2 weeks → suggest promotions; fission new evergreens into atomic idea stubs |
| `/backlog` | Rank the wiki's unresolved-link demand into a "notes to write" queue (the archive asking for its next note) |
| `/decide <question>` | Load relevant beliefs + past decisions → run decision framework |
| `/calibrate` | Grade matured decisions in one line (agent drafts the outcome, you confirm) so judgment compounds |
| `/weekly` | Summarize last 7 daily notes, surface themes, suggest focus areas |
| `/contradict` | Find conflicting beliefs or decisions that don't align with beliefs |
| `/dialectic [topic]` | Argue a topic (or today's captures) with the five critical-thinking personas, two rounds, then synthesize |
