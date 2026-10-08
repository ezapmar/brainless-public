---
lang: en
summary_en: Trunk-based development policy for the brainless vault. Single linear master, fast-forward-only pushes, a sync protocol every automated writer follows, single-writer file ownership, union merge for append-only files, secrets hygiene, and a freshness SLO watched by the watchdog.
status: draft
created: 2026-09-06
---

# Trunk-Based Development Policy

**Pattern name:** trunk-based development (TBD) with linear history, fast-forward-only pushes, and single-writer file ownership. In a human team this document would be `CONTRIBUTING.md` plus `CODEOWNERS`; here the contributors are two machines, so it is a sync protocol.

**Incident that triggered it:** on 2026-09-04 the Mac wrote the morning briefing at 09:31 and did not push until 21:30. omarchy ran the evening closeout at 21:01 against stale state and created the same file. The add/add conflict made every Mac push fail for 51 hours. The backup job aborted the rebase on each run and never recovered. Resolved by hand on 2026-09-06.

## 1. Branching model

- One long-lived branch: `master` (the trunk). No feature branches, no pull requests.
- History is linear. Integration is `git pull --rebase --autostash`. Merge commits are not allowed.
- Pushes are fast-forward only. Force push is forbidden. The only exception is a history rewrite to purge private data; each one is logged, with what was removed and how it was checked, in `_Agent-Context/TRUNK-EXCEPTIONS.md`, which stays private and never ships.

## 2. Writers and ownership (single-writer principle)

| Writer | Role | Owns | Push cadence |
|---|---|---|---|
| Mac | primary author | human areas (Tunca's edits), `.wiki/` compile, morning briefing, `_Agent-Context/` | hourly: the hourly job commits everything, Tunca's edits included, and pushes. 21:30: the backup job does the same and then refreshes the semantic index |
| omarchy | 24/7 worker | `Inbox/`, `Thinking/Daily/`, evening closeout, `CONTEXT-DRIFT.md`, `.wiki/relationships/`, `.wiki/digests/queries/`, `DIALECTIC-STATUS.md` (dialectic rounds) | immediately after each job |

Rules:
- Every file has exactly one owner. The owner is the only process that creates or overwrites it.
- A file that both machines must touch (the daily briefing) is append-only: non-owners add a section, never create or replace.
- Bots write to human areas only through the Mac backup job (Tunca's own edits) or after explicit approval (thinking loop "apply").

## 3. Sync protocol (every automated writer)

1. Check reachability with a batch-mode SSH probe. If GitHub is down, skip the pull, commit locally, exit 0.
2. If a rebase is in progress from a previous run, abort it and notify. Never leave the repo locked.
3. `git pull --rebase --autostash origin master`.
4. Write. Stage only the paths you own. Commit.
5. **The job that writes is the job that pushes.** Local-only state older than a few minutes is the root cause of the incident above.
6. Retry the push up to three times with backoff; re-sync between attempts.

On the Mac, `vault_backup.sh` is the only script that pushes. The hourly job (`cron_wrapper.sh`) calls it, scoped (`--only <paths>`) for the morning briefing and in full (`--no-index`) for everything else, so the network wait, the private-remote guard and the retry live in one place. The guard asks the GitHub API whether the remote is private and refuses the push if it cannot tell; it tries three times, because the first HTTPS request after a wake from sleep can fail.

**Incident behind the hourly push:** on 2026-10-07 the Mac was asleep at 21:30. The backup ran at 22:44 as it woke, the guard's single request failed (HTTP 000) and the only push of the day was refused. The hourly job kept committing and ten commits stayed local until the next morning.

## 4. Conflict policy

- Append-only paths (`Daily Briefings/`, `Thinking/Daily/`): resolve by concatenation, Mac content first, omarchy content appended. Declare this in `.gitattributes` so git does it automatically:

```
Daily\ Briefings/*.md merge=union
Thinking/Daily/*.md merge=union
```

  Verify the union driver also covers the add/add case on the current git version before relying on it.
- Everything else: abort, notify, resolve by hand on the Mac within 24 hours.
- A resolution is complete only when `git status -sb` reports neither ahead nor behind.

## 5. Commit message convention

- Automated commits: `vault backup: <timestamp>` (Mac), `vault backup: <timestamp> (omarchy)`, `telegram capture: <timestamp> (omarchy)`, `Auto-process: <what>`.
- The machine suffix is mandatory. The watchdog attributes commits by it.
- Human and agent-authored commits are written in Hemingway style. Subject line under 50 characters, imperative, no period. Body in short declarative sentences: what changed, why, what it breaks if anything. No adjectives, no jargon, no bullet lists. One idea per sentence.

Example:

```
Add trunk-based development policy

Two machines write to one trunk. They fought over a file.
This says who owns what, when to push, and how to merge.
```

## 6. Secrets and size hygiene

- Tokens, IBANs, health data, and OAuth files never enter the repo. They live in `~/.config/brainless/` and `.agents/state/` (gitignored).
- Files over 95 MB are auto-ignored before staging; GitHub hard-rejects blobs over 100 MB.
- On a leak: filter-repo, force push once, rotate the credential, log the exception. Do not try to patch history with new commits.

## 7. Observability and SLO

- Freshness SLO: the newest Mac-attributed commit on GitHub is at most 26 hours old. The watchdog alerts on breach.
- Unpushed commits on the Mac: the health check warns from one and turns red at ten. With the hourly push, more than two or three means several hourly runs in a row failed to push.
- Triage order on alert: 1) `logs/vault_backup.log` for "push refused", push rejections or "rebase aborted" lines, 2) `launchctl list | grep brainless` for the last exit code, 3) machine asleep or off.
- Two consecutive "rebase aborted" lines mean divergence. The scripts do not self-heal by design; resolve by hand.

## Open decisions

- Adopt the `merge=union` attribute for append-only paths, or keep manual resolution?

## Decided

- 2026-10-02: the hourly job pushes the morning briefing as soon as the file exists.
- 2026-10-08: the hourly job commits and pushes everything, Tunca's Obsidian edits included. The 21:30 backup stays as a second chance and as the run that refreshes the semantic index. A note or an agent session caught mid-edit is committed as it stands; the next run commits the rest.
