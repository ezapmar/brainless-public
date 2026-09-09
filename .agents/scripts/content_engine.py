#!/usr/bin/env python3
"""Haftalik icerik motoru (worker, Sali 09:00).

Haftanin malzemesinden (Spiky raporlari, okunan linkler, gunluk capture'lar)
uretim-rehberi'ndeki kurucu sesiyle 2-3 LinkedIn taslagi uretir.
Taslaklar Inbox/Content Drafts/ altina duser, ozet Telegram'a gider.
Yayinlamaz; sadece taslak onerir, karar sahibin.
"""
import os
import sys
from datetime import datetime, timedelta

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
sys.path.insert(0, os.path.join(VAULT, "tools"))
sys.path.insert(0, os.path.join(VAULT, ".agents", "scripts"))
from llm import run_prompt
from owner_profile import OWNER, OWNER_FULL, WORKER  # noqa: E402
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
        log("Bu hafta malzeme yok")
        return
    guide = read_file(GUIDE, 6000)
    prompt = f"""Sen {OWNER} için içerik asistanısın. Aşağıdaki haftalık malzemeden, üretim rehberindeki sese sadık kalarak 2-3 LinkedIn post taslağı yaz.

KURALLAR:
- Rehberdeki ses ve format kuralları esastır; kurucu sesi, kişisel gözlem + net fikir.
- Şirket içi hassas detay (finansal rakamlar, müşteri adları, M&A görüşmeleri) ASLA kullanılmaz; bunlardan ancak anonimleştirilmiş genel ders çıkarılabilir.
- Her taslak: "## Taslak N: <başlık>" + hangi malzemeden doğduğu tek satır + post metni.
- Em dash ve en dash kullanma.
- Sadece taslakları döndür.

# ÜRETİM REHBERİ:
{guide}

# HAFTANIN MALZEMESİ:
{material[:30000]}"""
    out = run_prompt(prompt, timeout=300)
    if not out:
        log("Taslak uretilemedi")
        return
    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(OUT_DIR, f"{stamp} haftalik taslaklar.md")
    with open(path, "w") as fh:
        fh.write(f"# Haftalık İçerik Taslakları ({stamp})\n\n"
                 f"Kaynak: content_engine.py ({WORKER}). Yayın kararı ve düzenleme {OWNER} tarafında.\n\n"
                 f"{out}\n")
    log(f"Taslaklar yazildi: {path}")
    titles = [l.strip("# ").strip() for l in out.splitlines() if l.startswith("## ")]
    send_telegram("✍️ Haftalık içerik taslakları hazır:\n" +
                  "\n".join(f"- {t}" for t in titles) +
                  "\n\nVault: Inbox/Content Drafts/" + os.path.basename(path))


if __name__ == "__main__":
    main()
