# Git policy: one writer, one commit a night

Since 2026-11-15 the Mac is the only machine that writes the vault. The two-writer
rules that lived here (who owns which path, pull before every job, conflict
recovery) are retired with the Linux worker.

- `master` is the only long-lived branch. Engine changes may use a short-lived
  branch or worktree and merge back the same week.
- Scheduled jobs never commit. `.agents/scripts/night.sh` ends with
  `vault_backup.sh`: one `git add -A`, one commit "vault backup: <date>", one push.
- Interactive sessions commit only when the owner asks.
- The push goes only to a private GitHub repo; `vault_backup.sh` refuses a public
  or unverifiable remote, and `.agents/state/no_push` pauses pushing.
- Private homes listed in `PROFILE.md` stay gitignored; `tools/tests/test_gitignore_guards.py`
  checks it.
- History rewrites are recorded in `TRUNK-EXCEPTIONS.md`.
