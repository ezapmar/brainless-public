# Changelog

All notable changes to the public brainless engine. Dates are the day of the public
push. The private vault this is exported from has its own history.

## Unreleased

- Add `docs/scripts.md`: a reference for every Python script in `tools/` and
  `.agents/scripts/`, each with a definition, a description and the design
  philosophy behind it, plus a map of how the scripts relate. Linked from the README.

## 0.1.5 (2026-09-18)

- Add the Today queue: at most one decision, one commitment, and one evidence
  review. Telegram supports preview, apply, edit, defer, and dismiss. Repeated
  deferrals ask for a blocker or smaller step; seven-day completion counts and
  source links appear in TODAY.md. The existing morning reminder sends the queue.
- Record approved decision outcomes in calibration and recover interrupted Today
  writes without duplicating entries. See `docs/today-queue.md`.
- Preserve original documents and raw conversions after processing. Failed,
  missing, or empty AI summaries now trigger retries; generated output is
  validated before replacing an existing summary.
- Apply project privacy checks to every descendant source file.
- Rebuild project mirrors when any permitted source changes, is added, renamed,
  or removed. Existing mirrors rebuild once under the new dependency policy.
- Keep kill criteria out of dashboard actions and open loops. Prefer explicit
  Next Action sections, now included in the project template.
- Add offline regression tests for document retention, project privacy, dashboard
  actions, and the Today queue; run them in public CI.
- Rewrite the README around one loop and add `docs/capture-flow.md` on the
  capture flow and the Obsidian network.
- Keep private finance tooling out of the export. `tools/export_public.py` now
  excludes the finance folder, because a fixture of real figures passes any word
  scan. Deployment-specific agent rules move to a private companion file that
  `_Agent-Context/AGENT-RULES.md` points to and the export never lists.
- Workers pull with `--autostash`, the cron wrapper commits its regenerated
  context blocks locally so the tree does not sit dirty, and the content engine
  reads the editing rules alongside the production guide.
- `VERSION` catches up: 0.1.4 shipped with the file still reading 0.1.3.

## 0.1.4 (2026-09-15)

- Dialectic scorecard. Round one is isolated (one Buzz root per persona, so nobody
  reads anyone else before answering); round two is one root that quotes every
  round one reply. Replies end with `Vote:` (YES, NO, CONDITIONAL) and `Number:`
  (NN%), round two also with `New evidence:`. `score_topic()` turns those lines into
  a deterministic per-topic scorecard (affirmation rate, who moved and whether they
  cited evidence, a unanimity warning) that is filed with the note, posted to the
  channel and fed to the synthesis. Rounds append to
  `.agents/state/dialectic_scores.jsonl`; the worker writes the rolling 30 day view
  to `_Agent-Context/DIALECTIC-SCORECARD.md` with two flags: sycophancy
  (affirmation above 60 percent) and a persona that never votes NO.
  `brainless dialectic --scorecard` prints it. Persona rules and the `/dialectic`
  command carry the same headings; redeploy the personas after upgrading.
- Kill criteria. Every project `notes.md` may carry a `## Kill Criteria` section of
  `- [ ] YYYY-MM-DD | condition | consequence` lines (the project template has it).
  `tools/kill_criteria.py` scans them deterministically: a past date with an open
  box is a breach. Breaches turn the new health check row red, are written to
  `_Agent-Context/KILL-CRITERIA.md` for the briefing, lead the dashboard's new
  Kill criteria section and the think surface's provocation; PROJECTS-ACTIVE
  shows each project's next criterion, and projects without one are listed.
- Vault topic fallback: on a day with no captures the evening dialectic argues one
  thing the vault is waiting on (a decision past review, a decided note without a
  prediction, a pending decision near review, a stale belief, a live question),
  rotated with a 14 day cooldown, instead of idling.
- Health check: a "Morning briefing" row goes yellow on weekdays after 08:30 when the
  day's briefing is missing or lacks the health block.
- Think surface: the cadence step says how many days it has waited when it exceeds
  two weeks.
- Every thinking command prompt now ends with a Loopback step that files its
  output through `file_query.py`.

## 0.1.3 (2026-09-11)

- The engine is English end to end: every comment, docstring, log line, help
  string, shell message, prompt and convention document. No Turkish left in code.
- Output language is a setting, any language: `output_lang` in `PROFILE.md` takes
  any ISO code. LLM prompts carry a language directive; deterministic strings
  (health labels, bot replies, generated headings, section names) come from
  `tools/locale/<code>/<script>.json` through `tools/i18n.py`, English as the
  fallback. Shipped locales: `en`, `tr`. Add a directory to add a language.
  See `docs/localization.md`.
- Scripts that parse headings they wrote earlier accept both the current
  language and English, so existing vaults keep working.
- Buzz identities renamed to English: `briefing`, `thinking`, `tasks`, `content`
  (key files under `~/.config/brainless/buzz/keys/`), and the owner env file is
  `assistant.env`. Existing relays rename the files once.
- Installer accepts any two or three letter language code.
- Document conversion calls markitdown as a Python library
  (`tools/markitdown_native.py`), not the CLI: one reused converter instance, no
  PATH discovery, no per file subprocess. `smart_processor.py` and
  `batch_markitdown.py` share it.
- No em or en dashes anywhere in the tree; the leak scan and CI stay as before.

## 0.1.2 (2026-09-11)

- CRM snapshot addon: read-only pull from Pipedrive into `_Agent-Context/CRM.md` and
  per-organisation event logs under `Inbox/CRM/`. Organisation and deal level only,
  no person data, no LLM. Provider seam for other CRMs. Systemd timer for the worker,
  hourly run on the laptop. Documented with a Pipedrive example in
  `docs/addons/crm-pipedrive.md`.
- Health check row for the CRM snapshot, and an optional Buzz `#crm` channel
  (`install_crm_channel.sh`, `buzz_crm_sync.sh`) that receives the block when it changes.
- `buzz_post.sh` no longer needs coreutils `timeout` on macOS.
- Flag severity: an organisation without an open deal is never red; red is reserved
  for deals.
- `VERSION` file and `brainless version`.
- `docs/` ships with the export.

## 0.1.1 (2026-09-10)

Not tagged at the time; tagged retroactively on the last commit of the day.

- One-line installer (`install.sh`) and the `brainless` command.
- README rewritten around the decision loop and the five voices, with the real
  three-device setup as a usage example.
- Calibration (`brainless calibrate`) and the think-surface command.
- Transcript filter, dialectic trigger, failure alerts on the worker.
- CI leak-scan gate: every push and pull request runs the export scan.

## 0.1.0 (2026-09-09)

- First public export: engine, commands, personas, installer, MIT license.
