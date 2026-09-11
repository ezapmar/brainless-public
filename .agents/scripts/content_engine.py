#!/usr/bin/env python3
"""Weekly content engine (worker, Tuesday 09:00).

From the week's material (Spiky reports, read links, daily captures) it
produces 2-3 LinkedIn drafts in the founder voice of the production guide.
Drafts land under Inbox/Content Drafts/, a summary goes to Telegram.
It never publishes; it only proposes drafts, the decision is the owner's.
"""
import os
import sys
from datetime import datetime, timedelta

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
sys.path.insert(0, os.path.join(VAULT, "tools"))
sys.path.insert(0, os.path.join(VAULT, ".agents", "scripts"))
from llm import run_prompt
from owner_profile import OWNER, OWNER_FULL, WORKER, output_lang_directive  # noqa: E402
from i18n import t  # noqa: E402
from watchdog import send_telegram

GUIDE = os.path.join(VAULT, "Personal", "Content", "content", "uretim-rehberi.md")
SOURCES = ["Inbox/Spiky", "Inbox/Links", "Thinking/Daily"]
OUT_DIR = os.path.join(VAULT, "Inbox", "Content Drafts")
LOOKBACK_DAYS = 7


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def read_file(path, limit=None):
    try:
        with open(path) as fh:
            s = fh.read()
        return s[:limit] if limit else s
    except OSError:
        return ""


def week_material():
    cutoff = datetime.now() - timedelta(days=LOOKBACK_DAYS)
    chunks = []
    for rel in SOURCES:
        d = os.path.join(VAULT, rel)
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d), reverse=True):
            p = os.path.join(d, f)
            if not f.endswith(".md") or datetime.fromtimestamp(os.path.getmtime(p)) < cutoff:
                continue
            chunks.append(f"### {rel}/{f}\n{read_file(p, 2200)}")
            if len(chunks) >= 20:
                break
    return "\n\n".join(chunks)


def main():
    material = week_material()
    if not material:
        log("No material this week")
        return
    guide = read_file(GUIDE, 6000)
    prompt = f"""You are the content assistant for {OWNER}. From the weekly material below, write 2-3 LinkedIn post drafts, staying faithful to the voice in the production guide.

RULES:
- The voice and format rules in the guide are binding; founder voice, personal observation + a clear idea.
- Sensitive company internals (financial figures, customer names, M&A talks) are NEVER used; at most an anonymised general lesson may be drawn from them.
- Each draft: "## {t('content_engine.draft_word')} N: <title>" + one line on which material it grew from + the post text.
- Return only the drafts.
- {output_lang_directive()}

# PRODUCTION GUIDE:
{guide}

# THIS WEEK'S MATERIAL:
{material[:30000]}"""
    out = run_prompt(prompt, timeout=300)
    if not out:
        log("Drafts could not be produced")
        return
    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(OUT_DIR, t("content_engine.drafts_filename", stamp=stamp))
    with open(path, "w") as fh:
        fh.write(t("content_engine.file_title", stamp=stamp) + "\n\n" +
                 t("content_engine.file_source_line", worker=WORKER, owner=OWNER) + "\n\n" +
                 f"{out}\n")
    log(f"Drafts written: {path}")
    titles = [l.strip("# ").strip() for l in out.splitlines() if l.startswith("## ")]
    send_telegram(t("content_engine.telegram_header") + "\n" +
                  "\n".join(f"- {x}" for x in titles) +
                  "\n\n" + t("content_engine.telegram_vault_line", filename=os.path.basename(path)))


if __name__ == "__main__":
    main()
