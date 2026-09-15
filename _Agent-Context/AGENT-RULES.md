# Agent Operating Rules

Section names and briefing text below are given in English; in a live vault they follow `output_lang` in `_Agent-Context/PROFILE.md` (the Turkish locale in `tools/locale/tr` keeps the original names, e.g. "Sistem Sağlığı" for "System Health").

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
- The morning half is produced by the Claude scheduled task `morning-briefing` (weekdays 07:00, `~/.claude/scheduled-tasks/morning-briefing/SKILL.md`; runs only while the desktop app is open). The evening half is appended by `tools/evening_closeout.py` on the worker at 21:00. A manual "briefing" session (or the equivalent word in the owner's language) follows the same convention and must merge into the existing file, never create a second one.
- Never write briefing files to the vault root or invent new name variants (morning-brief, morning-memo, etc. are retired).
- Every briefing must start with a 3-line "System Health" block sourced from `_Agent-Context/HEALTH.md` (written hourly by `tools/health_check.py`). If HEALTH.md reports a red flag, put it at the top of the briefing.
- If `_Agent-Context/CRM.md` exists (written hourly by `.agents/scripts/crm_capture.py`, read-only pull from the CRM, no LLM), add a "CRM: Enterprise Line" section after the open promises: copy its "Flags" and "Changes (last 24h)" bullets verbatim. Skip the section when both say "none". A 🔴 CRM status joins the health block at the top. Ad hoc CRM questions and the Business Development screening use the Pipedrive MCP connector interactively, never the briefing. The same snapshot is posted to the Buzz channel `#crm` (identity `crm`, `.agents/scripts/buzz_crm_sync.sh`) whenever its body changes; that channel is the place to discuss accounts and the Business Development line with the assistant.
- Read `_Agent-Context/KILL-CRITERIA.md` (written hourly by `tools/kill_criteria.py` through the health check). If its Breached list is not empty, put those lines verbatim, in red, directly under the System Health block: the owner wrote down in advance when to stop and the date has passed. Add the "Due within 14 days" lines under the open promises. Never soften or reinterpret a breached line; the decision (apply the consequence or move the date with a reason) is the owner's.
- If `_Agent-Context/RESURFACE.md` is less than 7 days old, include its 5 notes as a short "Revisit This Week" section.
- If `_Agent-Context/CONTEXT-DRIFT.md` reports drift (anything other than "No drift"), mention it in the briefing and ask the owner whether to apply the proposed CONTEXT.md updates.
- Read `_Agent-Context/DIALECTIC-STATUS.md` and add one line: "Yesterday's dialectic: N topics, M/K persona replies, affirm NN%, unanimous a/b, moved c/d" (the numbers are in the status line) with a link to the filed note in `.wiki/digests/queries/` (the evening run, or the noon run if the evening did not happen). If yesterday has no line or the result is `error`, write "Dialectic round did not run" in red next to the health block. If `_Agent-Context/DIALECTIC-SCORECARD.md` lists a flag under "## Flags" (sycophancy above 60 percent, or a persona that never votes NO), repeat that flag in one line; it means the debate is agreeing with the owner too easily.

---

## CRM Data: Privacy and Masking

Scope: everything that originates in the CRM (Pipedrive today): the hourly snapshot (`_Agent-Context/CRM.md`), the event logs (`Inbox/CRM/`), the Buzz channel `#crm`, interactive work through the CRM's MCP connector, and every note, digest, briefing or message derived from any of these. The CRM is the system of record; the vault holds a minimised, derived copy. Legal frame: KVKK for the Turkish entity, UK GDPR for the UK entity. Working principle: data minimisation.

### Three tiers

| Tier | What | Rule |
|---|---|---|
| A: organisation and deal | Organisation name, deal title, pipeline, stage, value and currency, dates (last activity, next step, expected close, stage change), lost reason, flags, deal owner (the owner's own staff), company-level custom fields (employee count, sector, HR tech stack, lead source, competitors), CRM deal and organisation ids and links | May be written wherever CRM output is allowed (see "Where it may go") |
| B: external contact persons | Name, job title, e-mail, phone, LinkedIn or other profile URL, CRM person id, anything else that identifies a person at a customer or partner | Never written. Refer to the person by role at the organisation ("their HR director", "the CFO there"). If one person must be followed through a single document, use a pseudonym that is stable inside that document only ("Contact 1, HR"). Detail stays in the CRM; cite the deal link instead |
| C: content that never leaves the CRM | Verbatim e-mail bodies and signatures, attachments, cc addresses, personal e-mail addresses, national ID numbers, IBAN or bank details, salary or payroll data of a customer's employees, tenant ids, credentials, API tokens | Never written, not even masked. If found in a note, drop it |

### How masking works

- Scheduled path (`.agents/scripts/crm_capture.py`): Tier A is enforced in the normaliser, not by a config flag. Person fields are never requested from the API. Do not add person fields to the provider mapping; any change to the field list runs `--self-test`, and the fixture keeps its invented organisation names.
- Interactive path (MCP connector): the assistant may read person data while reasoning about an account. Everything it writes (loopback digest, Buzz message, briefing line, draft for a human area) is reduced to Tier A plus Tier B masking before it is filed. Each digest says so in its scope section and links every row to its CRM record so the detail can be looked up at the source.
- Quotes: a short customer statement from a note or e-mail may be quoted when it carries no speaker identity ("we were looking for a card-based system"). Never quote a header, a signature, or a sentence that names the writer.
- Internal staff (deal owners, sales leads, a former owner in a reassignment): name and role are allowed; their personal contact details are not.
- Deal and organisation titles are copied as they stand in the CRM, because they are the organisation-level identifier; a sole trader's business name is a business name. A deal whose title is a bare person's name is a CRM data quality issue: rename it in the CRM to the organisation, do not mask it in the vault.
- The HR platform connector has its own pseudonym masking for employee data; this section covers the CRM only.

### Where it may go

| Destination | Allowed |
|---|---|
| `_Agent-Context/CRM.md`, `Inbox/CRM/`, Buzz `#crm`, `Daily Briefings/` (copied verbatim from CRM.md) | Tier A only, produced by the script |
| `.wiki/digests/queries/*crm*`, `.wiki/summaries/` built from `Inbox/CRM/` | Tier A, Tier B masked |
| Human areas (`Work/`, `Thinking/`) | Same masking, and only after the owner's "yes, apply" (rule 2) |
| Public repo (`tools/export_public.py`) | Nothing CRM-derived. `Inbox/`, `Daily Briefings/`, `.wiki/digests/` and `_Agent-Context/CRM.md` are already excluded; keep them excluded. Docs and fixtures use invented organisation names only |
| Assistant memory (`~/.claude/projects/<vault>/memory/`) | Tier A facts about the pipeline are fine; never Tier B or C |
| Chat transcripts | Never paste a token or a contact's details. A token that lands in a transcript is regenerated the same day |

### Retention and repair

- `Inbox/CRM/` logs are event-only and stay as long as the organisation is tracked; the compiler summarises them like any Inbox note.
- Tier B content found in the private repo: remove it in a normal commit and note the fix in that day's briefing.
- Tier C content (a secret or special-category data) found in the repo: rewrite history (git filter-repo, force-push) and rotate the secret.
- A deletion or correction request from a customer or contact is handled in the CRM; the vault copy follows on the next run, or is removed by hand if it sits in a digest.

---

## Critical Dialectic (Buzz)

- `tools/dialectic.py` runs on the worker at 12:30 and 21:20 (`brainless-dialectic.timer`). It clusters the day's unseen Telegram and Buzz captures into topics, posts each topic in the Buzz channel `#dialectic` as the `moderator` identity, and mentions five live persona agents: Skeptic (Browne & Keeley), Gambler (Duke), Scientist (Camuffo 2024), Postmortem (Edmondson), Strategist (Lafley & Martin). Two rounds (method critique, then rebut the strongest objection), then an LLM synthesis posted in the thread and filed to `.wiki/digests/queries/<date>-dialectic-<slug>.md`. All engine names, prompts and notes are in English (`lang: en`).
- Personas are read-only harnesses (`buzz-persona@<slug>.service`, prompts in `.agents/buzz/personas/`, installer `.agents/buzz/install_personas.sh`). They answer only the moderator and the owner and never mention anyone, so agents cannot trigger each other. The owner can mention any persona in any thread for an ad-hoc answer.
- The engine only proposes (seed, belief, decision stubs). Nothing is written to `Thinking/`; rule 2 holds.
- `/dialectic [topic]` is the local twin of the same rounds (`.wiki/_commands/dialectic.md`).

## Thinking Loop (Telegram)

- `.agents/scripts/thinking_loop.py --ask` runs Sundays 19:00 on the worker (`brainless-thinking.timer`, unit files in `.agents/systemd/`). It sends ONE question by priority: decision due to grade, decision without a prediction, next Thinking Cadence step, stalest belief, then a seed prompt if no idea was captured in 14 days.
- The owner answers by replying to that message (voice or text). `telegram_capture.py` routes the reply to the loop, which drafts the note change and sends a preview. Only "apply" writes to `Thinking/`; "cancel" drops the draft; "skip" skips the question for the week (the words are per language in `tools/locale/<lang>/thinking_loop.json`).
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
