#!/usr/bin/env python3
"""Weekly thinker/source digest (worker, Friday 07:00).

Plan 7. Scans the RSS/Atom feeds listed in `_Agent-Context/thinkers.md`, finds
new posts, fetches the FULL TEXT of the few most important ones and synthesizes
them into a single weekly note, tying each to the owner's projects.

Design notes:
- X/Twitter is NOT scanned automatically (auth-gated in 2026, no free API,
  scraping is both fragile and a ToS violation). MANUAL arm for X-native
  authors: the owner sends the thread link to the bot, link ingest drops it
  into Inbox/Links and this digest folds that week's Links notes into its
  synthesis as well.
- The first run establishes a BASELINE (it does not choke on 219 Paul Graham
  essays); afterwards it flows.
- NO urgent push (owner decision): everything is collected in the weekly digest.
"""
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
sys.path.insert(0, os.path.join(VAULT, "tools"))
sys.path.insert(0, os.path.join(VAULT, ".agents", "scripts"))
from llm import run_prompt
from owner_profile import OWNER, OWNER_FULL, WORKER, LANG, output_lang_directive  # noqa: E402
from i18n import t  # noqa: E402
from watchdog import send_telegram
from telegram_capture import fetch_page_text        # SSRF-protected fetcher

REGISTRY = os.path.join(VAULT, "_Agent-Context", "thinkers.md")
STATE_FILE = os.path.join(VAULT, ".agents", "state", "thinker_seen")
LINKS_DIR = os.path.join(VAULT, "Inbox", "Links")
OUT_DIR = os.path.join(VAULT, ".wiki", "digests")
CONTEXT_FILE = os.path.join(VAULT, "_Agent-Context", "CONTEXT.md")
MAX_FULL_TEXT = 6          # number of posts whose full text is fetched (cost cap)
MAX_NEW_PER_FEED = 5       # a single feed must not flood the digest in one week
SEEN_CAP = 4000


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def read(path):
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return ""


def parse_registry():
    """-> [(name, feed_url|None, topic)]"""
    out = []
    for line in read(REGISTRY).splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "|" not in s:
            continue
        parts = [p.strip() for p in s.split("|")]
        if len(parts) < 2 or parts[0].startswith("Format:"):
            continue
        feed = parts[1] if parts[1] and parts[1] != "-" else None
        out.append((parts[0], feed, parts[2] if len(parts) > 2 else ""))
    return out


def _text(el):
    return (el.text or "").strip() if el is not None else ""


def fetch_feed(url):
    """Read RSS and Atom the same way -> [(title, link)], newest first."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (brainless)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read(1_500_000)
    root = ET.fromstring(raw)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    items = []
    for it in root.iter():
        tag = it.tag.split("}")[-1]
        if tag == "item":                                   # RSS
            title, link = _text(it.find("title")), _text(it.find("link"))
            if title and link:
                items.append((title, link))
        elif tag == "entry":                                # Atom
            title = _text(it.find("a:title", ns)) or _text(it.find("title"))
            le = it.find("a:link", ns)
            link = (le.get("href") if le is not None else "") or _text(it.find("link"))
            if title and link:
                items.append((title, link))
    return items


def recent_link_notes():
    """Notes dropped from links sent to the bot in the last 7 days (MANUAL arm)."""
    out = []
    if not os.path.isdir(LINKS_DIR):
        return out
    cutoff = datetime.now() - timedelta(days=7)
    for f in sorted(os.listdir(LINKS_DIR), reverse=True):
        p = os.path.join(LINKS_DIR, f)
        if not f.endswith(".md"):
            continue
        try:
            if datetime.fromtimestamp(os.path.getmtime(p)) < cutoff:
                continue
        except OSError:
            continue
        out.append((f, read(p)[:3000]))
    return out[:8]


def main():
    # Keep it ordered: trimming must drop the OLDEST first (trimming a set drops
    # random urls and an old post still sitting in the feed comes back as "new").
    seen_list = [l for l in read(STATE_FILE).splitlines() if l.strip()]
    seen = set(seen_list)
    first_run = not seen
    fresh, errors = [], []

    for name, feed, topic in parse_registry():
        if not feed:
            continue
        try:
            items = fetch_feed(feed)
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__}")
            continue
        unseen = [(ti, l) for ti, l in items if l not in seen]
        if first_run:
            # Baseline: count everything STILL SITTING in the feed as seen. Marking
            # only the first few would leak the rest of the archive as "new" the
            # following week (like the 219 old essays on Paul Graham).
            for _, l in unseen:
                seen.add(l); seen_list.append(l)
            continue
        for ti, l in unseen[:MAX_NEW_PER_FEED]:
            seen.add(l); seen_list.append(l)
            fresh.append({"who": name, "topic": topic, "title": ti, "url": l})

    def save_seen():
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as fh:
            fh.write("\n".join(seen_list[-SEEN_CAP:]))

    if first_run:
        save_seen()
        log(f"Baseline established ({len(seen_list)} records); new posts flow from next week.")
        if errors:
            log("feed errors: " + "; ".join(errors))
        return

    links = recent_link_notes()
    if not fresh and not links:
        log("no new content")
        return

    # Fetch the full text of the newest few (the rest stay at headline level).
    bodies = []
    for item in fresh[:MAX_FULL_TEXT]:
        try:
            txt = fetch_page_text(item["url"])
        except Exception:
            txt = ""
        if txt:
            bodies.append(f"### {item['who']}: {item['title']}\n{item['url']}\n{txt[:6000]}")

    headlines = "\n".join(f"- {i['who']} ({i['topic']}): {i['title']} -> {i['url']}"
                          for i in fresh) or "(no new posts in the feeds)"
    manual = "\n\n".join(f"### (link you sent) {f}\n{b}" for f, b in links) or "(none)"
    context = (read(CONTEXT_FILE) or "")[:2500]

    prompt = f"""You are the assistant that prepares the weekly reading synthesis for {OWNER}.
Below are this week's posts by the tracked thinkers and the links {OWNER} sent in personally.

RULES:
- Each item: what it says + WHY it matters for {OWNER} (tie it to the projects, use [[wikilink]]).
- Write only the genuinely valuable ones; no filler, dismiss the weak ones in a single line.
- If something is unrelated to {OWNER}'s agenda, you are free to say "unrelated" and move on.
- At the end, under a "What to do this week" heading, at most 3 concrete suggestions.
- {output_lang_directive()}
- Return only the markdown body (do NOT write frontmatter).

# CONTEXT FOR {OWNER}:
{context}

# PUBLISHED THIS WEEK (headlines):
{headlines}

# SELECTED FULL TEXTS:
{chr(10).join(bodies)[:30000]}

# LINKS SENT TO THE BOT (manual arm, X included):
{manual[:12000]}
"""
    out = run_prompt(prompt, timeout=300)
    if not out:
        log("synthesis not produced; state not updated (retried next week)")
        return

    stamp = datetime.now().strftime("%Y-%m-%d")
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"thinkers-{stamp}.md")
    with open(path, "w") as fh:
        fh.write(f"---\nlang: {LANG}\nsummary_en: Weekly synthesis of tracked thinkers "
                 f"and links {OWNER} forwarded, with relevance to active projects.\n"
                 f"compiled_at: {datetime.now().isoformat(timespec='seconds')}\n"
                 f"type: digest\n---\n" + t("thinker_digest.digest_title", stamp=stamp) + "\n\n"
                 + t("thinker_digest.digest_source_line", worker=WORKER, n_new=len(fresh),
                     n_links=len(links)) + f"\n\n{out.strip()}\n")
    log(f"Digest written: {path}")

    save_seen()

    who = sorted({i["who"] for i in fresh})
    links_part = t("thinker_digest.notify_links_part", n_links=len(links)) if links else ""
    send_telegram(t("thinker_digest.notify_ready", n_new=len(fresh), links_part=links_part)
                  + (t("thinker_digest.notify_authors", who=", ".join(who)) if who else "")
                  + t("thinker_digest.notify_vault", path=f".wiki/digests/thinkers-{stamp}.md"))
    if errors:
        log("feed errors: " + "; ".join(errors))


if __name__ == "__main__":
    main()
