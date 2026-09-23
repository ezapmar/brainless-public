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

## Answer contract
When answering from the vault, every claim cites the page it came from as `[[page]]`. For "what do I know about X" questions, open `.wiki/concepts/` first. Anything the model knows that the vault does not contain goes under a separate heading, "Outside the vault" ("Vault dışı"). Every answer ends with two lines: `Read: [[...]]` and `Not covered: ...`. If the vault has nothing, say so in one sentence and do not fill the gap from general knowledge. The reason: a blended answer is fluent and untraceable, and the gap line is often the more useful half, because it says exactly what to read next.

## Merging duplicates
`tools/lint_wiki.py` lists merge proposals in `.wiki/_lint-report.md` (from `tools/wiki_dedupe.py`). It never merges. When the owner approves one:
1. Pick the survivor by its canonical name, not by which page is longer.
2. Keep every distinct claim with its source. Overlapping claims collapse into one.
3. Add the dead page's title to the survivor's aliases.
4. Point every inbound link at the survivor.
5. Log it in `.wiki/_archive/LOG.md` with both names.

Run merges as a pass of their own, on a clean git state, and never during an ingest. For a "clash" proposal (an entity and a project mirror with the same name), rename one of the two instead of merging them.

## Enforced by hooks
`.claude/settings.json` runs `tools/hooks/claude_guard.py` on every tool call. It denies em and en dashes (in files, file names, commands and commit messages), secrets and misplaced briefings. It asks before a write into a human area or any deletion. It sends back a `.wiki` page that lacks `lang` or `summary_en`, and it loads CONTEXT.md and PROJECTS-ACTIVE.md at session start. When a hook refuses, fix the output rather than working around the hook.

## Contradictions and supersession
When a source disagrees with what a wiki page says, record both positions with their sources and dates, and say what would settle it. Never overwrite, and never pick the newer source because it is newer: recency is not evidence. When a claim is simply out of date, strike it through and keep it: `~~old claim~~ superseded YYYY-MM: why ([[source]])`. The reason: the history of what was believed and why is what this vault has that a search engine does not, and a page that silently adopts the latest claim loses it without anything erroring. Claims on concept pages carry a confidence label: primary, secondary, self-reported, unverified. Self-reported numbers never lose that label. `tools/concepts.py` enforces the keep-both and keep-struck parts on every compile.

## Concept pages
`.wiki/concepts/` holds one page per idea, updated in place by the compiler from the registry `_Agent-Context/concepts.md`. For "what do I know about X" questions, open the concept page first, then its sources. A concept page answers; a summary is evidence.

## No hallucinated files
If a note, path, or line you want to cite doesn't actually exist in the vault, say so. Do not invent file names that "should" exist. "I couldn't find X" is a valid and valuable answer.

## Flag drift
If you notice that `_Agent-Context/CONTEXT.md` or `_Agent-Context/PROJECTS-ACTIVE.md` contradicts what recent daily notes say, flag it, don't quietly trust the context file.

## Concise output
Lead with the answer. No preamble ("Sure! I'll now…"). No trailing summary of what you just did. The owner can read. Prefer tables and lists over prose when the output is structured.

## Scope discipline
Only do what the command asks. Don't volunteer extra analysis, refactoring suggestions, or "while I was at it, I also noticed…" unless the command explicitly invites it.

## CRM data
Anything derived from the CRM (the snapshot `_Agent-Context/CRM.md`, `Inbox/CRM/`, MCP connector output) follows the "CRM Data: Privacy and Masking" section of `_Agent-Context/AGENT-RULES.md`: organisation and deal level only, external contacts by role and never by name, no e-mail bodies or contact details, every row linked to its CRM record. Say in the digest's scope section that this masking was applied.
