# Obsidian Web Clipper Templates & Theme Setup

Ready-to-import Obsidian Web Clipper JSON templates pre-configured for `brainless` (`Inbox/Links/` with `status_checked`, `source`, `tags`, and `privacy: internal` frontmatter).

## 1. Import into Obsidian Web Clipper
1. Install the official **Obsidian Web Clipper** browser extension (uses `kepano/defuddle` under the hood).
2. Open Web Clipper Settings -> **Templates** -> **Import**.
3. Import:
   - `docs/clipper-templates/article.json` (general articles)
   - `docs/clipper-templates/x-post.json` (auto-triggers on `x.com` / `twitter.com`)
   - `docs/clipper-templates/youtube.json` (auto-triggers on YouTube videos)
4. Every clip lands in `Inbox/Links/` as clean Markdown and is picked up by the 23:00 `night.sh` compile chain.

## 2. Optional Visual Theme (Kepano Layer 4: Minimal + Flexoki)
Your `.obsidian/appearance.json` was left untouched so your current look does not change unexpectedly. To enable Steph Ango's Minimal + Flexoki setup in Obsidian:
1. Open Obsidian -> **Settings -> Appearance -> Themes -> Manage**.
2. Install and activate **Minimal** (`kepano/obsidian-minimal`).
3. Install the **Minimal Theme Settings** community plugin (or Style Settings) and pick **Flexoki** (`kepano/flexoki`) as the colour scheme.

## 3. Activate Controlled Gmail Intake
1. In Gmail, create a label named `Brain` (matching `.agents/scripts/gmail_brain_intake.py`).
2. Label any e-mail `Brain` that you want captured into your vault.
3. The hourly job (`cron_wrapper.sh`) polls `Brain` over read-only IMAP using your existing `~/.config/brainless/gmail_address` and `gmail_app_password`, saves traceable notes to `Inbox/Mail/` with `privacy: personal` (compiled locally on Gemma 4 12B), tracks processed Message-IDs in `.agents/state/gmail_intake_state.json` (zero duplicates), and never alters Gmail.
4. Test manually at any time:
   ```bash
   python3 .agents/scripts/gmail_brain_intake.py --dry-run
   ```
