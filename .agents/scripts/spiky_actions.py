#!/usr/bin/env python3
"""Spiky raporlarindan aksiyon cikarimi -> _Agent-Context/TASKS.md (worker).

Yeni Inbox/Spiky notlarini Claude'a verir; SADECE sahibin kendi sozleri
("Sözlerim") ve baskasina delege ettigi isler ("Bekliyorum") cikarilir.
Satir formati (gtasks_sync.py bunu parse eder):
  - [ ] Gorev metni | [[Toplanti notu adi]] | YYYY-MM-DD
  - [ ] Kisi: Gorev metni | [[...]] | YYYY-MM-DD   (Bekliyorum bolumu)

State: .agents/state/spiky_actions_done (islenmis dosya adlari).
--backfill N: son N gunun raporlarini isler (ilk kurulum icin).
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
sys.path.insert(0, os.path.join(VAULT, "tools"))
from llm import run_prompt
from owner_profile import OWNER, OWNER_FULL, WORKER  # noqa: E402

SPIKY_DIR = os.path.join(VAULT, "Inbox", "Spiky")
TASKS_FILE = os.path.join(VAULT, "_Agent-Context", "TASKS.md")
STATE_FILE = os.path.join(VAULT, ".agents", "state", "spiky_actions_done")
CONTEXT_FILE = os.path.join(VAULT, "_Agent-Context", "CONTEXT.md")
MAX_PER_RUN = 10

HEADER = """# Görev Defteri

Tek görev defteri. Toplantı sözleri Spiky raporlarından (spiky_actions.py), günlük capture aksiyonları nightly digest'ten (nightly_processor.py) otomatik düşer; ikisi de worker'de.
Kapatmak için checkbox'ı işaretle veya Google Tasks'ta tamamla; 30 dk içinde iki yön senkronlanır.

## Sözlerim

## Bekliyorum
"""


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def read_file(path):
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return None


def note_date(fname):
    m = re.match(r"(\d{4}-\d{2}-\d{2})", fname)
    return m.group(1) if m else None


def open_task_titles(tasks_md):
    return [m.group(1).split(" | ")[0].strip()
            for m in re.finditer(r"- \[ \] (.+)", tasks_md or "")]


def extract(note_name, note_text, existing):
    context = (read_file(CONTEXT_FILE) or "")[:3000]
    existing_block = "\n".join(f"- {t}" for t in existing[:60]) or "(bos)"
    prompt = f"""Sen {OWNER} için görev defterini besleyen bir çıkarım asistanısın.
Aşağıdaki Spiky toplantı raporundan SADECE şunları çıkar:
1. "ben": {OWNER} adlı sahibin KENDİ üstlendiği işler (raporlarda "{OWNER_FULL}: ..." diye geçer).
2. "delege": {OWNER} adlı sahibin başkasına verdiği/beklediği işler; kişi adıyla.

KURALLAR:
- {OWNER} adlı sahibi ilgilendirmeyen maddeleri atla (iki başka kişi arasındaki işler dahil).
- Muğlak/genel maddeleri atla ("durum değerlendirilecek" gibi sahipsiz laflar).
- Transkripsiyon isim hatalarını bağlama göre düzelt.
- MEVCUT DEFTERDEKİ maddelerle aynı/çok benzer olanları TEKRAR YAZMA.
- Görev metni kısa ve eylem odaklı olsun (en fazla ~12 kelime).
- SADECE geçerli JSON döndür, başka hiçbir şey yazma. Şema:
[{{"sahip": "ben" veya "delege", "kisi": "delege ise kişi adı, yoksa boş", "gorev": "..."}}]
- Çıkarılacak madde yoksa [] döndür.

# MEVCUT DEFTER (tekrar etme):
{existing_block}

# {OWNER} İÇİN BAĞLAM (isim düzeltmeleri için):
{context}

# RAPOR ({note_name}):
{note_text[:9000]}"""
    out = run_prompt(prompt, timeout=180)
    if not out:
        return []
    m = re.search(r"\[.*\]", out, re.S)
    if not m:
        return []
    try:
        items = json.loads(m.group(0))
    except ValueError:
        log(f"{note_name}: JSON parse edilemedi")
        return []
    return [i for i in items if isinstance(i, dict) and i.get("gorev")]


def _clean_field(s, limit=140):
    """LLM alanini tek satira indir: newline/pipe/[[ ]] enjeksiyonunu kes.
    Aksi halde TASKS.md parser'i, Google Tasks ve Telegram zehirlenebilir."""
    s = re.sub(r"[\r\n\t]+", " ", str(s))
    s = s.replace("|", "/").replace("[[", "(").replace("]]", ")")
    return re.sub(r"\s{2,}", " ", s).strip()[:limit]


def append_tasks(tasks_md, items, note_name, date_str):
    lines = tasks_md.splitlines(keepends=True)
    link = os.path.splitext(note_name)[0]
    for item in items:
        gorev = _clean_field(item["gorev"])
        if item.get("sahip") == "delege" and item.get("kisi"):
            text = f"{_clean_field(item['kisi'], 40)}: {gorev}"
            section = "## Bekliyorum"
        else:
            text = gorev
            section = "## Sözlerim"
        row = f"- [ ] {text} | [[{link}]] | {date_str}\n"
        for idx, l in enumerate(lines):
            if l.strip() == section:
                end = idx + 1
                while end < len(lines) and not lines[end].startswith("## "):
                    end += 1
                while end > idx + 1 and lines[end - 1].strip() == "":
                    end -= 1  # bolum sonundaki bos satirin ustune ekle
                lines.insert(end, row)
                break
    return "".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", type=int, default=0, metavar="GUN")
    args = ap.parse_args()

    done = set((read_file(STATE_FILE) or "").splitlines())
    cutoff = None
    if args.backfill:
        cutoff = (datetime.now() - timedelta(days=args.backfill)).strftime("%Y-%m-%d")

    candidates = []
    for f in sorted(os.listdir(SPIKY_DIR) if os.path.isdir(SPIKY_DIR) else []):
        if not f.endswith(".md") or f in done:
            continue
        d = note_date(f)
        if cutoff and (not d or d < cutoff):
            done.add(f)  # backfill penceresi disi: islenmis say
            continue
        if not args.backfill and (not d or d < (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")):
            done.add(f)  # normal modda 7 gunden eski yeni-gorunen dosyalari atla
            continue
        candidates.append(f)

    tasks_md = read_file(TASKS_FILE) or HEADER
    for f in candidates[:MAX_PER_RUN]:
        note = read_file(os.path.join(SPIKY_DIR, f)) or ""
        items = extract(f, note, open_task_titles(tasks_md))
        if items:
            tasks_md = append_tasks(tasks_md, items, f, note_date(f) or "")
            log(f"{f}: {len(items)} madde")
        else:
            log(f"{f}: madde yok")
        done.add(f)

    os.makedirs(os.path.dirname(TASKS_FILE), exist_ok=True)
    with open(TASKS_FILE, "w") as fh:
        fh.write(tasks_md)
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as fh:
        fh.write("\n".join(sorted(done)))
    if len(candidates) > MAX_PER_RUN:
        log(f"{len(candidates) - MAX_PER_RUN} dosya sonraki tura kaldi")


if __name__ == "__main__":
    main()
