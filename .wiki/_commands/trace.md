# /trace: Project Traceability Audit

Audit one active project end-to-end from raw/human sources through compiled `.wiki/` pages, open promises, contradictions, drafts, and governing rules.

## 1. Identify the project
- Match the requested project name against `_Agent-Context/PROJECTS-ACTIVE.md` and its hub `notes.md` (under `Work/` or `Personal/`).
- Check `_Agent-Context/PROFILE.md` for `cloud_exclude` or `protected_homes` status and note the privacy boundary at the top of the report.

## 2. Walk the traceability chain
Inspect every link in the chain without modifying any human area:
1. **Primary sources:** Hub `notes.md`, recent files in the project folder, recent `Inbox/` (`Inbox/Spiky/`, `Inbox/Mail/`, `Inbox/CRM/`), and `Thinking/Daily/` mentions.
2. **Compiled layer:** Matching `.wiki/summaries/`, `.wiki/projects/`, `.wiki/entities/`, `.wiki/concepts/`, and `.wiki/digests/queries/`. Check `compiled_at` vs source mtime for staleness.
3. **Promises and kill criteria:** Matching open items in `_Agent-Context/TASKS.md` and `_Agent-Context/KILL-CRITERIA.md`.
4. **Contradictions and drift:** Matching entries in `_Agent-Context/CONTRADICTIONS.md`, `_Agent-Context/CONTEXT-DRIFT.md`, and `_Agent-Context/ATTENTION.md`.
5. **Outputs and drafts:** Recent drafts in `Writings/Drafts/` or `Daily Briefings/` referencing this project.
6. **Governing rules:** Relevant rules from `_Agent-Context/LEARNINGS.md` and `_Agent-Context/AGENT-RULES.md`.

## 3. Report structure
Produce a concise audit report in the vault's output language (`output_lang` in `PROFILE.md`) with zero em/en dashes:
- **Project & Privacy Tier:** Hub link `[[...]]`, `status_checked: YYYY-MM-DD HH:MM`, privacy tier.
- **Chain Map:** `Source -> .wiki/ summary -> TASKS/KILL-CRITERIA -> Draft/Output`, marking every broken or stale link.
- **Mutable Facts Needing Live Recheck:** Dates, amounts, owners, external statuses older than 7 days.
- **Contradictions & Attention Gaps:** Disagreements between sources, or mismatch between stated priority and recent activity.
- **Proposed Next Actions (for the owner's approval):** Concrete edits or rule updates, never applied automatically.
- **Read:** `[[...]]`
- **Not covered:** Explicit list of missing or inaccessible sources.
