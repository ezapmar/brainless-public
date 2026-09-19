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
- Pushes are fast-forward only. Force push is forbidden. Two precedents, both history rewrites to purge private data, both logged here: 2026-08-28 (leaked secrets) and 2026-09-19 (see below). Any future exception is logged the same way.

### Exception log: 2026-09-19 history purge

**Why.** An audit of `.gitignore` found that rules naming a single path die silently when the path is renamed, and that history still carried 131 paths the current rules forbid: compiled summaries of passports and identity documents, `* - Health` folders, `Work/Kolay IK/Finance/Resources` and its wiki summaries, and a child's records. `.gitignore` does not untrack, and it does not reach backwards, so the working tree being clean said nothing about the history.

**What was done.** `git filter-repo --invert-paths` over 15 path globs, run on a throwaway clone rather than the live repo, then force-pushed and both machines hard-reset to the new master. 770 commits before and after; only blobs and paths were removed.

**Checks that made it safe.** A full `git bundle --all` backup was taken first and kept at `~/Library/Caches/brainless-backup/` on the Mac. The rewritten HEAD tree hash was compared against the original and is identical, so nothing currently tracked was lost; the file count is 2426 on both sides. omarchy was verified clean and fully pushed beforehand, so no worker commit was orphaned.

**What this does not fix.** GitHub keeps unreferenced objects reachable by SHA for a while, and any existing clone or fork still holds the old history. Treat anything that was in there as disclosed to whoever had a copy, and rotate rather than assume.

### Exception log: 2026-09-19 second purge (binaries and bank details)

**Why.** Two things the first pass did not cover. The vault had been pushing original documents to git for months, 194 MB of PDFs and scans that never diff and can never be deleted; and the company account number, sort code and IBAN sat in plain text in eight tracked files, masked in the working tree earlier that evening but still in every old commit.

**What was done.** `git filter-repo` on a throwaway clone with two inputs: 764 binary paths to drop, and a `--replace-text` list redacting the account number, both IBAN spellings and the driving licence number wherever they appear. Force-pushed, both machines resynced. 777 commits before, 776 after (one became empty and was pruned). Local `.git` went from 984 MB to 45 MB in the rewritten clone.

**Three mistakes worth remembering, all caught before the push.** Collecting paths with `--diff-filter=A` misses renamed files, because git reports them as `R`; that lost 179 paths. `git rev-list --objects` prints each blob once under a single path, so a renamed file's old path survives; that lost another 310. And deleting a branch is not optional: two stale session branches still pointed at the old history, which would have kept every purged blob alive on GitHub. Their work was already in master and the rest was regenerable `.wiki/`, so they were deleted after checking.

**The check that mattered.** After each attempt, the rewritten HEAD tree hash was compared against the original. It matched every time, which is what proves the rewrite removed history and not content.

**Still true from the first purge.** Old clones and GitHub's unreferenced objects keep what was removed. The account details should be treated as disclosed and rotated if that ever matters.

Recommended repo config on both machines:

```bash
git config pull.rebase true
git config rebase.autoStash true
git config merge.ff only
git config push.default simple
```

## 2. Writers and ownership (single-writer principle)

| Writer | Role | Owns | Push cadence |
|---|---|---|---|
| Mac | primary author | human areas (Tunca's edits), `.wiki/` compile, morning briefing, `_Agent-Context/` | 21:30 backup job (currently the only push) |
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
- Triage order on alert: 1) `logs/vault_backup.log` for push rejections or "rebase aborted" lines, 2) `launchctl list | grep brainless` for the last exit code, 3) machine asleep or off.
- Two consecutive "rebase aborted" lines mean divergence. The scripts do not self-heal by design; resolve by hand.

## Open decisions

- Which job pushes the morning briefing: the hourly processor or the briefing script itself?
- Adopt the `merge=union` attribute for append-only paths, or keep manual resolution?
- Should Tunca's Obsidian edits wait for the 21:30 push, or should the hourly job push as well?
