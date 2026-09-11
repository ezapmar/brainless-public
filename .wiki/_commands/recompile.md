---
name: recompile
description: Force re-summarize a raw source into wiki/.
argument-hint: <raw-path-or-glob> [--full]
---

## Behavior

1. Resolve argument to file path(s) under `raw/` or the company area in `Work/`.
2. Run `python3 tools/compile_resources.py --only summaries` if a single source, or set source mtime to now via `touch <path>` then run incremental compile.
3. For `--full`: `python3 tools/compile_resources.py --full-rebuild`.
4. After compile, run `/lint` and report any new issues.
5. File a record at `.wiki/digests/queries/<date>-recompile-<slug>.md`.
