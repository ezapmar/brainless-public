#!/usr/bin/env python3
"""Dusunur/kaynak haftalik digest'i (worker, Cuma 07:00).

Plan 7. `_Agent-Context/thinkers.md` listesindeki RSS/Atom feed'lerini tarar,
yeni yazilari bulur, en onemli birkacinin TAM METNINI ceker ve sahibin
projeleriyle bagini kurarak tek bir haftalik nota sentezler.

Tasarim notlari:
- X/Twitter OTOMATIK TARANMAZ (2026'da auth-gated, ucretsiz API yok, scraping
  hem kirilgan hem ToS ihlali). X-yerlisi yazarlar icin MANUEL kol: sahip
  thread linkini bota atar, link ingest onu Inbox/Links'e dusurur ve bu digest
  o haftanin Links notlarini da sentezine katar.
- Ilk calisma BASELINE kurar (219 Paul Graham denemesini bocalamaz), sonrasi akar.
- Acil push YOK (sahip karari): her sey haftalik digest'te toplanir.
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
from owner_profile import OWNER, OWNER_FULL, WORKER  # noqa: E402
from watchdog import send_telegram
from telegram_capture import fetch_page_text        # SSRF korumali fetcher

REGISTRY = os.path.join(VAULT, "_Agent-Context", "thinkers.md")
STATE_FILE = os.path.join(VAULT, ".agents", "state", "thinker_seen")
LINKS_DIR = os.path.join(VAULT, "Inbox", "Links")
OUT_DIR = os.path.join(VAULT, ".wiki", "digests")
CONTEXT_FILE = os.path.join(VAULT, "_Agent-Context", "CONTEXT.md")
MAX_FULL_TEXT = 6          # tam metni cekilecek yazi sayisi (maliyet siniri)
MAX_NEW_PER_FEED = 5       # tek feed bir haftada digest'i basmasin
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
    """-> [(ad, feed_url|None, konu)]"""
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
    """RSS ve Atom'u tek sekilde oku -> [(baslik, link)] en yeni once."""
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
    """Son 7 gunde bota atilan linklerden dusen notlar (MANUEL kol)."""
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
    # Sirali tut: kirpma en ESKIden yapilsin (set kirpmak rastgele url atar ve
    # feed'de duran eski yazi "yeni" gibi geri gelir).
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
        unseen = [(t, l) for t, l in items if l not in seen]
        if first_run:
            # Baseline: feed'de HALEN DURAN her seyi gorulmus say. Sadece ilk
            # birkacini isaretlemek, sonraki hafta arsivin geri kalanini "yeni"
            # diye sizdirirdi (Paul Graham'da 219 eski deneme gibi).
            for _, l in unseen:
                seen.add(l); seen_list.append(l)
            continue
        for t, l in unseen[:MAX_NEW_PER_FEED]:
            seen.add(l); seen_list.append(l)
            fresh.append({"who": name, "topic": topic, "title": t, "url": l})

    def save_seen():
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as fh:
            fh.write("\n".join(seen_list[-SEEN_CAP:]))

    if first_run:
        save_seen()
        log(f"Baseline kuruldu ({len(seen_list)} kayit); yeni yazilar haftaya akar.")
        if errors:
            log("feed hatalari: " + "; ".join(errors))
        return

    links = recent_link_notes()
    if not fresh and not links:
        log("yeni icerik yok")
        return

    # En yeni birkacinin tam metnini cek (gerisi baslik duzeyinde kalir).
    bodies = []
    for item in fresh[:MAX_FULL_TEXT]:
        try:
            txt = fetch_page_text(item["url"])
        except Exception:
            txt = ""
        if txt:
            bodies.append(f"### {item['who']}: {item['title']}\n{item['url']}\n{txt[:6000]}")

    headlines = "\n".join(f"- {i['who']} ({i['topic']}): {i['title']} -> {i['url']}"
                          for i in fresh) or "(feed'lerde yeni yazi yok)"
    manual = "\n\n".join(f"### (senin attigin link) {f}\n{b}" for f, b in links) or "(yok)"
    context = (read(CONTEXT_FILE) or "")[:2500]

    prompt = f"""Sen {OWNER} için haftalık okuma sentezini hazırlayan asistanısın.
Aşağıda izlenen düşünürlerin bu hafta yayımladıkları ve {OWNER} adlı sahibin kendi attığı linkler var.

KURALLAR:
- Her madde: ne söylüyor + {OWNER} için NEDEN önemli (projeleriyle bağ kur, [[wikilink]] kullan).
- Sadece gerçekten değerli olanları yaz; dolgu yapma, zayıf olanları tek satırda geç.
- Bir şey {OWNER} adlı sahibin gündemiyle ilgisizse "ilgisiz" deyip geçmek serbesttir.
- Sonda "Bu hafta ne yapmalı" başlığı altında en fazla 3 somut öneri.
- Em dash ve en dash KULLANMA.
- Sadece markdown gövde döndür (frontmatter YAZMA).

# {OWNER} İÇİN BAĞLAM:
{context}

# BU HAFTA YAYIMLANANLAR (başlıklar):
{headlines}

# SEÇİLMİŞ TAM METİNLER:
{chr(10).join(bodies)[:30000]}

# BOTA ATILAN LİNKLER (manuel kol, X dahil):
{manual[:12000]}
"""
    out = run_prompt(prompt, timeout=300)
    if not out:
        log("sentez uretilemedi; state guncellenmedi (gelecek hafta tekrar denenir)")
        return

    stamp = datetime.now().strftime("%Y-%m-%d")
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"thinkers-{stamp}.md")
    with open(path, "w") as fh:
        fh.write(f"---\nlang: tr\nsummary_en: Weekly synthesis of tracked thinkers "
                 f"and links {OWNER} forwarded, with relevance to active projects.\n"
                 f"compiled_at: {datetime.now().isoformat(timespec='seconds')}\n"
                 f"type: digest\n---\n# Haftalık Okuma Sentezi ({stamp})\n\n"
                 f"Kaynak: thinker_digest.py ({WORKER}). {len(fresh)} yeni yazi, "
                 f"{len(links)} forward edilen link.\n\n{out.strip()}\n")
    log(f"Digest yazildi: {path}")

    save_seen()

    who = sorted({i["who"] for i in fresh})
    send_telegram(f"📚 Haftalık okuma sentezi hazır ({len(fresh)} yeni yazı"
                  + (f", {len(links)} senin linkin" if links else "") + ")\n"
                  + ("Yazanlar: " + ", ".join(who) + "\n" if who else "")
                  + f"\nVault: .wiki/digests/thinkers-{stamp}.md")
    if errors:
        log("feed hatalari: " + "; ".join(errors))


if __name__ == "__main__":
    main()
