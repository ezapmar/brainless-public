# brainless architecture

Since 2026-11-15 brainless runs on one Mac. There is no second machine, no chat
relay and no VPN. This page is the whole system; if it disagrees with the code,
the code wins and this page is wrong.

```
iPhone / iPad ──Telegram──▶ [Mac] capture.sh (every 2 min)
                                 ├─ plain message   → note in Thinking/Daily or Inbox/Links
                                 ├─ reply to bot    → reply queue → interactions.py (approve / follow up)
                                 └─ "?question"     → reply queue → read-only cited answer (local model)
iPhone / iPad ◀─iCloud Drive─▶ [Mac] the vault (Obsidian)
[Mac] hourly    cron_wrapper.sh  CRM snapshot, Spiky mail, Gmail "Brain" label intake,
                                 Google Tasks, meeting brief, morning set once a day
                                 after 07:00 (including model-free morning briefing), health check
[Mac] 23:00     night.sh         ingest → nightly → close-out → compile → index → links
                                 → lint (orphans, broken, dead-ends) → attention audit
                                 → Sunday weeklies (dreaming, prune, research, kill review,
                                 weekly archive) → health → one commit + push
[Mac] always    llama-server     Gemma 4 12B on 127.0.0.1:8080, the local lanes
GitHub (private)                 off-site copy only, one push a night
```

## Jobs

Four launchd jobs, installed by `bash .agents/launchd/install.sh` (rerun it after
changing a script path; `--uninstall` removes them). All of them source
`.agents/scripts/env.sh`, the one place for PATH and PYTHONPATH. Model routing
lives in `brainless.toml` at the vault root, not in `env.sh`, so a job or an agent
that never sourced `env.sh` still sees the same lanes.

| Job | When | Script | Writes git? |
|---|---|---|---|
| capture | every 120 s | `.agents/scripts/capture.sh` → `telegram_capture.py` → `tools/interactions.py` | no |
| hourly | every 3600 s | `.agents/scripts/cron_wrapper.sh` | no |
| night | 23:00, or on wake if missed | `.agents/scripts/night.sh` | yes, once |
| llm | always on | `llama-server` | no |

launchd never starts a second copy of a running job. A hand-started run can
still overlap the scheduled one, so `night.sh` holds a pid lock in
`.agents/state/night.pid` and a second shift exits at once. Only `night.sh`
(through `vault_backup.sh`) commits, so there is one writer and no conflict path.
A night that ran as a next-morning catch-up skips the evening close-out, which
would otherwise be filed under the wrong date. `.agents/state/night_last.json`
records how the last night went; `health_check.py` turns it red after two days.

Two Python lanes: Homebrew's `python3` for the stdlib-only steps, and the 3.13
venv (`BRAINLESS_SEARCH_PY`, see `env.sh`) for everything that needs a wheel.
Ingest (markitdown), the nightly compile (contradiction check and retrieval score),
the semantic index and links, and dreaming run in the venv; install its packages
with `requirements-core.txt`, `requirements-search.txt` and
`requirements-google.txt`. Nothing is installed into the system Python.

## Morning, mail and attention

- **Model-free morning briefing** (`tools/morning_briefing.py`): runs in the first
  hourly job after 07:00 on weekdays (catches up on wake) and writes the top block
  of `Daily Briefings/daily-briefing-YYYY-MM-DD.md` from files other jobs already
  wrote (health, breached kill criteria, calendar, open promises, CRM flags, wiki
  changes, resurface, drift, top actions). No model, no open desktop app required.
- **Controlled Gmail intake** (`.agents/scripts/gmail_brain_intake.py`): polls a
  dedicated Gmail label (`Brain` by default) over read-only IMAP. Records processed
  Message-IDs in `.agents/state/gmail_intake_state.json` only after writing
  `Inbox/Mail/YYYY-MM-DD-<slug>.md` atomically, so re-runs produce zero duplicates
  and Gmail stays untouched. Notes are tagged `privacy: personal` so they compile
  on the local model only. Exits cleanly until the label exists.
- **Attention audit** (`tools/attention_audit.py`): runs nightly with no model and
  writes `_Agent-Context/ATTENTION.md`, comparing stated active projects in
  `PROJECTS-ACTIVE.md` against actual 7-day file activity while excluding all
  protected homes and private segments.
- **Lint with dead-end detection** (`tools/lint_wiki.py`): checks broken links,
  orphans, dead-ends (inbound links, zero outbound links), stale summaries,
  frontmatter and model chatter every night.

## Telegram

`tools/notify.py` is a durable outbox on the bot. Every message is queued in
`.agents/state/outbox.sqlite3` before it is sent, retried with backoff, and sent
at most once per key. The first line of a message is its channel label
(`#tasks`, `#thinking`, `#dreaming`, `#ops`, `#inbox`, ...). A reply is routed by
the label of the message it answers, so the today queue, the thinking loop,
dreaming and concept review keep their own approval rules
(`tools/today_reply.py`, `tools/thinking_reply.py`, `tools/dreaming.py`,
`tools/concept_review.py`). Telegram cannot say whether a timed-out send was
accepted, so a lost acknowledgement can produce one duplicate notice; it never
repeats a vault write.

## Models

Every prompt goes through `tools/llm.py` with a lane name (`python3 tools/llm.py --lanes`),
configured in `brainless.toml` (template: `brainless.toml.example`).

- Local (Gemma 4 12B, `openai-compatible` on loopback, listed in `local_only`):
  `note-classify`, `dreaming-judge`, `compile-aliases`, `compile-local` (summary,
  entity, concept and assignment work for a cloud-excluded or `privacy: personal`
  source), `capture-note`, `capture-photo` (the image is attached; measured 2.9 s
  for a clean printed page), `spiky-actions`, `ask`.
  A local first pass for the contradiction check (lane `contradiction-screen`)
  exists and is switched off: on its first night it cleared two pages that held
  real conflicts.
- Claude CLI (subscription): `compile`, `compile-concept`, `nightly`, `close-out`,
  `reconcile`, `resurface`, `research`, `thinking loop`, `digests`, `content`.
  These write the pages everything else reads; a weak writer caps what any reader
  can get back.
- A lane listed in `local_only` fails closed. If its provider resolves to a cloud
  one (an env override, a typo, a missing block) the call is refused and logged;
  if the local server is down the job fails and retries, and the text does not
  leave the Mac.

To move a lane, edit `brainless.toml` after checking agreement with Claude on
real samples (`docs/local-inference.md`).

## Privacy: what may reach a cloud provider

Three layers, and each one exists because the layer before it does not cover the
whole problem.

1. **Redaction** (`tools/redact.py`): `llm.py` masks e-mail, phone, IBAN, national
   IDs and key shapes in every prompt bound for a cloud provider. This is a pattern
   gate, so it cannot see a school name, an address, a family relationship or a
   diagnosis.
2. **Cloud exclusion** (`tools/cloud_gate.py`): folders listed under `cloud_exclude`
   in `_Agent-Context/PROFILE.md`, and single notes whose frontmatter says
   `privacy: personal` or `privacy: secret`, never reach a cloud provider.
   `privacy: personal` is compiled on the local lane (`compile-local`);
   `privacy: secret` is not compiled at all. The compiler picks the lane
   (`_compile_lane`), and `llm.py` refuses the call if a blocked path reaches a
   cloud lane anyway (`source_violation`). Both read the same list, so a folder
   added to `PROFILE.md` changes both at once.
3. **Egress ledger** (`.agents/state/egress.jsonl`): one line per call with the
   lane, provider, model, character counts, redaction counts, the source paths
   hashed (never written) and whether the call was sent. `health_check.py` turns
   red when a `cloud_exclude` folder is missing from disk or when a cloud line
   from the last 24 hours carries an excluded source.

Cross-links survive boundary 2: a concept page whose evidence is partly
cloud-excluded keeps a one-line note naming the project it belongs to, and the
summary, the path and the body stay on the Mac.

## Interactive agents and guardrails

- **Claude Code hooks** (`.claude/settings.json` + `tools/hooks/claude_guard.py`):
  `raw/` is strictly denied for writes and edits; human areas (`Work/`, `Personal/`,
  `Library/`, `Thinking/`, `Inbox/`, `Writings/` outside drafts) ask before writing;
  secrets, em/en dashes and misplaced briefings are denied; `.wiki/` writes are
  checked for `lang:` and `summary_en:`.
- **Agents** (`.claude/agents/`): `scout.md` (read-only explorer for `Inbox/`,
  `raw/` and `.wiki/`) and `librarian.md` (deep `.wiki/` curator enforcing
  `cloud_exclude`, conflict callouts and a 15-page edit cap per source).
- **Slash commands** (`.wiki/_commands/` + `.claude/commands/`): `/decide`,
  `/research`, `/weekly`, `/context`, `/lint`, `/reply` (Thread to Reply into
  `Writings/Drafts/`), and `/trace` (end-to-end project traceability audit).

## Mobile

The vault is opened from iCloud Drive in Obsidian on the iPhone and iPad.
Capture, questions and approvals go through Telegram, so the phone needs no
engine at all.

## Backups

- Git history, pushed nightly to a private GitHub repo (the push is refused
  when the repo is public or cannot be checked).
- `tools/vault_archive.py`: a weekly age-encrypted archive outside the synced
  folder (`--if-older-days 6` in Sunday's night shift; health turns yellow at 9
  days and red at 21), the copy that a bad sync cannot overwrite.
