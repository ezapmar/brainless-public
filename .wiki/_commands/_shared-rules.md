# Shared Rules: every command must follow these

## Ownership rules (post-Karpathy migration)
- Human-owned homes: `Work/`, `Personal/`, `Library/`, `Thinking/`, `Inbox/`, `raw/`. NEVER write here without explicit "yes, apply" from the owner. These four (`Work/`, `Personal/`, `Library/`, `Thinking/`) are also the actual ingest roots the compiler reads from; `raw/` is the static import corpus, `Inbox/` the live capture buffer.
- `.wiki/` is LLM-owned. You MAY write/regenerate files here as part of normal operation.
- `_Agent-Context/`, `Archive/`, `Daily Briefings/`: shared.
- Root schema file: `CLAUDE.md` (thin pointer layer; this file and `_Agent-Context/AGENT-RULES.md` remain the sources of truth).

When a command produces output, FILE the output back into `.wiki/digests/queries/<YYYY-MM-DD>-<command>-<slug>.md` so future queries can see it. This is the loopback that makes the wiki compound. Use the helper: pipe your output to `python3 tools/file_query.py <command> "<title>"`.

## Cross-link rule
The owner's cross-link rule lives in `_Agent-Context/PROFILE.md` (section "Cross-link rule"). Apply it: whenever one domain of the rule appears, link to the other.

## Always cite sources
When you refer to a note, use its `[[Wikilink]]` form and include the full path on first mention, e.g. `[[CONTEXT]]` (`_Agent-Context/CONTEXT.md`). If you claim a note says something, quote the exact line. Never paraphrase as if it were the original.

## Respect maturity tags
- `#status/seed`, raw, unreliable. Do not treat as ground truth.
- `#status/growing`, partial. Good for suggestions, not for decisions.
- `#status/evergreen`, stable. Weight heavily.
- Untagged notes, treat as growing unless clearly a template or log.

## Speak the vault's language
Language policy: wiki output is written in the vault's output_lang (PROFILE.md) with an English summary block (summary_en). Frontmatter must include `lang:` and `summary_en:` on every wiki file. Match source language for raw quotations.

## Use the search tool
Before grepping by hand for broad queries, call `python3 tools/wiki_search.py "<query>" --json --k 10` and use the ranked results. Falls back to ripgrep + simple BM25.

## Use existing conventions
- Tag format: `#type/xxx`, `#status/xxx`, lowercase kebab-case.
- Link format: `[[Note Name]]` (no `.md`), section links `[[Note#Section]]`.
- Date format: `YYYY-MM-DD`.
- Frontmatter: match the template in `_Templates/` for the relevant note type.

## No hallucinated files
If a note, path, or line you want to cite doesn't actually exist in the vault, say so. Do not invent file names that "should" exist. "I couldn't find X" is a valid and valuable answer.

## Flag drift
If you notice that `_Agent-Context/CONTEXT.md` or `_Agent-Context/PROJECTS-ACTIVE.md` contradicts what recent daily notes say, flag it, don't quietly trust the context file.

## Concise output
Lead with the answer. No preamble ("Sure! I'll now…"). No trailing summary of what you just did. The owner can read. Prefer tables and lists over prose when the output is structured.

## Scope discipline
Only do what the command asks. Don't volunteer extra analysis, refactoring suggestions, or "while I was at it, I also noticed…" unless the command explicitly invites it.
