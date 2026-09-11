# Localization

brainless is written in English. What the owner reads is written in the language
set by `output_lang` in `_Agent-Context/PROFILE.md` (any ISO code, `en` by default;
`BRAINLESS_OUTPUT_LANG` overrides it).

Two mechanisms carry that setting:

| Text | Mechanism |
|---|---|
| Anything an LLM writes (summaries, digests, briefings, persona replies) | The prompt is English and ends with `output_lang_directive()` from `tools/owner_profile.py`, which names the language. Any language the model can write works. |
| Deterministic strings (health labels, bot replies, generated headings, section names, keyword lists such as the words that mean "apply" or "cancel") | `tools/i18n.py` reads `tools/locale/<code>/<script>.json`. A key missing from a language falls back to English. |

## Adding a language

1. Copy `tools/locale/en/` to `tools/locale/<code>/`.
2. Translate the values you care about; delete the rest (English fills the gaps).
3. Set `output_lang: <code>` in `PROFILE.md`.
4. `python3 tools/i18n.py` lists what is still missing per language.

Keys are flat per script, `{placeholders}` are filled with `str.format`, list values
are merged with English by `t_list` so a script accepts both languages when it parses
text it wrote earlier (for example a task ledger with `## Promises` or the local
heading).

Do not put language into code: no hardcoded labels, no language checks. Add a key.
