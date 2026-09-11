---
name: lint
description: Run wiki integrity checks (orphans, broken links, stale summaries, frontmatter).
argument-hint: (none)
---

## Behavior

1. Run `python3 tools/lint_wiki.py` (report-only; safe in automation).
2. Optionally run `python3 tools/lint_wiki.py --fix` (or `--fix --dry-run` first) to backfill missing frontmatter on legacy wiki files.
3. Read the resulting `.wiki/_lint-report.md`.
4. Summarize the top 3 most actionable issues. Lead with counts. Distinguish "real broken" from external/raw references and suppressed _commands/ template noise.
5. If broken links cluster around renamed/moved projects or people, propose a small mapping or note that many point at raw/ or company-area content (intentional).
6. File this summary into `.wiki/digests/queries/<date>-lint.md`.

## Output

```
# Lint: <date>

- broken: N | orphans: N | stale: N | fm_issues: N

## Top 3 actions
1. ...
2. ...
3. ...

## Proposed link rewrites
| from | to |
|---|---|
```
