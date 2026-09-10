# CRM snapshot addon (Pipedrive example)

A read-only pull from your CRM into the vault, so the daily briefing can say "these
three deals are stale" without you opening the CRM. Deterministic, no LLM, no person
data. Pipedrive is the first provider; the script has a seam for others.

Script: `.agents/scripts/crm_capture.py`

## What it produces

| Path | What | When it is written |
|---|---|---|
| `_Agent-Context/CRM.md` | Status block: flags, one table per pipeline, changes in the last 24h, organisations without open deals | Only when the body changes |
| `Inbox/CRM/<Organisation>.md` | One event log per organisation (deal created, stage moved, value changed, won, lost, reassigned) | Only when an event happens. A quiet run touches nothing under `Inbox/`, so the compiler is not woken for nothing |
| `.agents/state/crm_snapshot.json` | Previous state, used for the delta | Every run (gitignored) |
| `.agents/state/crm_status` | `<iso>\t<ok|auth|error>\t<detail>`, read by the health check | Every run |

The briefing copies the status block. The event logs are ordinary Inbox notes, so the
nightly compile summarises them and links them to the organisation's project notes.

## Privacy boundary

The provider mapping passes organisation and deal fields only. Person fields (names,
e-mails, phones, `person_id`, `cc_email`) are never requested from the API and never
written. This is enforced in the normaliser, not in a config flag, so a future provider
cannot leak them by accident.

## Setup (Pipedrive)

1. Get your personal API token in Pipedrive: profile menu, then
   *Personal preferences*, then *API*. It is a per-user token; the script only sees
   what that user sees.
2. Store it outside the repo, readable by you only:

   ```bash
   mkdir -p ~/.config/brainless
   umask 077
   printf '%s\n' 'YOUR_PIPEDRIVE_API_TOKEN' > ~/.config/brainless/pipedrive_api_token
   ```

3. Optional settings, one file each in `~/.config/brainless/`:

   | File | Meaning | Default |
   |---|---|---|
   | `crm_provider` | Provider name | `pipedrive` |
   | `crm_pipelines` | Pipeline names to include, one per line, case-insensitive | all pipelines |
   | `crm_owner` | Numeric user id whose organisations and deals are tracked | the token's owner |

   Example: track only two pipelines for the token owner.

   ```bash
   printf 'Enterprise\nBusiness Development\n' > ~/.config/brainless/crm_pipelines
   ```

4. Dry run first. It calls the API, prints the status block and the events, and
   writes nothing:

   ```bash
   python3 .agents/scripts/crm_capture.py --dry-run --lang en
   ```

5. Real run. The first run takes a baseline; deltas begin with the second run:

   ```bash
   python3 .agents/scripts/crm_capture.py
   ```

Without a token file the script exits 0 and does nothing, so every scheduler below is
safe to install before you decide to use the addon.

The company domain (`yourcompany.pipedrive.com`) is read from the API on every run
and never stored.

## Scheduling

- **Laptop.** The hourly `cron_wrapper.sh` job installed by `install.sh --schedule`
  already runs the snapshot. Nothing to add.
- **Always-on worker.** `.agents/systemd/brainless-crm.timer` runs it daily at 06:30
  through `worker_job.sh`, which pulls and pushes around it:

  ```bash
  systemctl --user enable --now brainless-crm.timer
  ```

  If both machines run it, the delta is still correct: state lives in the vault and
  travels with git.

## Health check and the briefing

- `tools/health_check.py` adds a "CRM snapshot" row to `_Agent-Context/HEALTH.md`
  once a token file exists: red when the last run failed, the token was rejected, or
  the last good run is older than 26 hours. No token, no row.
- The briefing convention (`_Agent-Context/AGENT-RULES.md`) copies the flags and the
  last-24h changes from `CRM.md` verbatim, and lifts a red CRM status into the health
  block at the top.

## Buzz channel (optional)

If you run the Buzz personas, the snapshot can also land in a `#crm` channel as its
own `crm` identity, so you can ask the assistant about an account where the numbers
are. Only a changed body is posted; a quiet hour posts nothing.

```bash
bash .agents/buzz/install_crm_channel.sh   # on the relay host: key, member, channel
```

`.agents/scripts/buzz_crm_sync.sh` does the posting. The laptop's hourly job calls it
right after the snapshot; on the worker add it as `ExecStartPost` to the CRM unit.
Without Buzz configured it exits 0.

## Flags

Flags are computed per deal, and for organisations that have no open deal.

| Code | Condition | Threshold flag |
|---|---|---|
| `NO ACTIVITY>Nd` | Days since the last activity exceed N | `--no-activity-days N` (14) |
| `NEXT OVERDUE` | Next scheduled activity date is in the past | |
| `CLOSE PASSED` | Expected close date is in the past | |
| `STAGE>Md` | Deal has sat in the same stage for more than M days | `--stage-days M` (30) |

Severity: a deal with no activity for more than twice the threshold is red, everything
else is yellow. An organisation without an open deal is never worse than yellow; red is
reserved for money in the pipeline. The block's top-line status is the worst flag.

## Example output

Produced from the built-in fixture with `--lang en` (the same data `--self-test`
uses). Organisation names are invented.

`_Agent-Context/CRM.md`:

```markdown
# CRM Status

**Status: 🔴 RED** (updated: 2026-09-11 06:30, source: pipedrive, owner: the owner)

- 3 organisations, 3 open deals, 5 flags, 0 changes in the last 24h

## Flags
- 🔴 Acme Holding: "Acme HRIS" no activity for 27 days, next step overdue (2026-09-05), expected close passed (2026-09-01), same stage for 53 days
- 🟡 Gamma Enerji no activity for 72 days

## Enterprise
| Organisation | Deal | Stage | Value | Last activity | Next step | Flags |
|---|---|---|---|---|---|---|
| Acme Holding | Acme HRIS | Proposal (53d) | 12,000 EUR | 2026-08-15 | 2026-09-05 | NO ACTIVITY>14d, NEXT OVERDUE, CLOSE PASSED, STAGE>30d |
| Beta Lojistik | Beta bordro | Discovery (10d) | 300,000 TRY | 2026-09-08 | 2026-09-15 | - |

Total: 2 deals, 12,000 EUR + 300,000 TRY

## Business Development
| Organisation | Deal | Stage | Value | Last activity | Next step | Flags |
|---|---|---|---|---|---|---|
| (no organisation) | Kanal ortakligi | Idea (2d) | 0 | 2026-09-09 |  | - |

Total: 1 deals

## Changes (last 24h)
- first run: baseline taken, deltas start with the next run

## Organisations without open deals
- Gamma Enerji (last activity 2026-07-01, NO ACTIVITY>14d)

> No person names, e-mails or phones by design (organisation and deal level). No LLM. Producer: .agents/scripts/crm_capture.py
```

`Inbox/CRM/Acme Holding.md` after the first run, then after the deal moved a stage:

```markdown
# Acme Holding

Source: CRM (pipedrive), automatic event log. No person data.

---

- 2026-09-11 06:30 Acme Holding: tracking started (1 open deals: "Acme HRIS" (Proposal))
- 2026-09-12 06:30 Acme Holding: "Acme HRIS" stage Proposal -> Negotiation
```

Set `output_lang: tr` in `_Agent-Context/PROFILE.md` (or pass `--lang tr`) and the
same block comes out in Turkish.

## Adding another provider

Subclass `Provider` in `crm_capture.py`, implement five methods, and register the class
in `PROVIDERS`. The rest of the script never sees provider fields.

| Method | Returns |
|---|---|
| `me()` | `{"id", "name", "company"}` for the token's user |
| `pipelines_and_stages()` | `{pipeline_id: {"name", "stages": {stage_id: name}}}` |
| `owner_organizations(owner_id)` | list of normalised organisations |
| `open_deals(owner_id)` | list of normalised open deals |
| `deal(deal_id)` | one normalised deal, or `None` if it is gone |

Normalised deal: `id, title, org_id, org_name, pipeline_id, stage_id, value, currency,
expected_close_date, last_activity_date, next_activity_date, stage_change_time,
add_time, update_time, label, status, owner_id`.
Normalised organisation: `id, name, open_deals_count, last_activity_date,
next_activity_date`.

Name the token file `<provider>_api_token` and set `crm_provider` to the new name.
Run `--self-test` after the change; it exercises the flag, delta and rendering logic
with a fake provider and needs no network.

## Troubleshooting

- `crm_status` says `auth`: the token was rejected (HTTP 401 or 403). Regenerate it in
  Pipedrive and replace the file.
- `crm_status` says `error`: the detail column has the exception. Rate limits (429) are
  retried once with the server's `Retry-After`.
- `crm_pipelines` matched nothing: the run logs a warning and falls back to all
  pipelines. Check the spelling against the pipeline names in the CRM.
- Nothing appears in the briefing: the briefing copies `_Agent-Context/CRM.md`; run
  `brainless health` and look for the CRM row.
