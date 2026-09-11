# Changelog

All notable changes to the public brainless engine. Dates are the day of the public
push. The private vault this is exported from has its own history.

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
