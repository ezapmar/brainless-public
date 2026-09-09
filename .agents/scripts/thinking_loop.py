#!/usr/bin/env python3
"""Telegram uzerinden dusunme dongusu (worker).

Amac: yansitma adimini (karar notlama, tahmin, inanc sinama, kademe adimi,
tohum fikir) sahibin fiilen cevap verdigi tek kanala, Telegram'a tasimak.

Akis:
  1. `--ask` (Pazar 19:00, brainless-thinking.timer): oncelik sirasina gore
     TEK soru secer, Telegram'a gonderir, bekleyen soruyu state'e yazar.
  2. Sahip o mesaja YANIT vererek (sesli ya da yazili) cevaplar; telegram_capture
     her turda `try_handle` ile once bu modulu dener. Cevap LLM'e gider, hedef
     nota islenecek TASLAK cikar; taslak onizleme olarak geri gonderilir.
  3. "uygula" -> taslak insan alanina (Thinking/) yazilir, degisim loopback'e
     dosyalanir. "iptal" -> taslak silinir. "gec" -> soru atlanir, sonraki
     hafta baska tur soru gelir.

Guvenlik: LLM yalnizca gomulu metin gorur, arac yok; cikti JSON olarak
ayristirilir ve alanlar temizlenir. Thinking/ altina yazma YALNIZCA "uygula"
sonrasi olur (AGENT-RULES kural 2: acik onay). Yazilan dosyalar sabit bir
listeden gelir; LLM dosya yolu secemez.
State: .agents/state/thinking_loop.json (gitignore altinda).
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
sys.path.insert(0, os.path.join(VAULT, "tools"))
sys.path.insert(0, os.path.join(VAULT, ".agents", "scripts"))
from llm import run_prompt  # noqa: E402
from owner_profile import OWNER, OWNER_FULL, WORKER  # noqa: E402

STATE_FILE = os.path.join(VAULT, ".agents", "state", "thinking_loop.json")
CONF_DIR = os.path.expanduser("~/.config/brainless")
DEC_DIR = os.path.join(VAULT, "Thinking", "Decisions")
BELIEF_DIR = os.path.join(VAULT, "Thinking", "Beliefs")
IDEAS_DIR = os.path.join(VAULT, "Thinking", "Ideas")
DAILY_DIR = os.path.join(VAULT, "Thinking", "Daily")
CADENCE = os.path.join(VAULT, "Thinking", "Thinking Cadence.md")
CALIBRATION = os.path.join(VAULT, "Thinking", "Calibration.md")
CONTEXT_FILE = os.path.join(VAULT, "_Agent-Context", "CONTEXT.md")
QUERIES_DIR = os.path.join(VAULT, ".wiki", "digests", "queries")

SEED_GAP_DAYS = 14        # bu kadar gun yeni tohum yoksa tohum sorusu
PENDING_TTL_DAYS = 13     # cevapsiz soru iki hafta sonra duser
KINDS_ORDER = ("grade", "predict", "cadence", "belief", "seed")
APPLY_WORDS = ("uygula", "apply", "yaz", "evet uygula")
CANCEL_WORDS = ("iptal", "cancel", "vazgec", "vazgeç")
SKIP_WORDS = ("gec", "geç", "skip", "atla")

FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] thinking_loop: {msg}")


def read(path):
    try:
        with open(path, errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(text)
    os.replace(tmp, path)


def load_state():
    try:
        with open(STATE_FILE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)


def fm(text):
    m = FM_RE.match(text)
    out = {}
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                out[k.strip()] = v.partition("#")[0].strip().strip('"')
    return out


def set_fm(text, key, value):
    """Frontmatter'da key satirini degistir ya da ekle (yorumlar silinir)."""
    m = FM_RE.match(text)
    if not m:
        return f"---\n{key}: {value}\n---\n" + text
    block = m.group(1)
    lines = block.splitlines()
    for i, line in enumerate(lines):
        if re.match(rf"^{re.escape(key)}\s*:", line):
            lines[i] = f"{key}: {value}"
            break
    else:
        lines.append(f"{key}: {value}")
    return text[:m.start(1)] + "\n".join(lines) + text[m.end(1):]


def clean(s, limit=600):
    """LLM alanini tek parcaya indir: yeni satirlar korunur ama link/yol/
    tire enjeksiyonu kesilir; em/en tire ev kurali geregi yasak."""
    s = str(s).replace("—", ", ").replace("–", "-")
    s = re.sub(r"[ \t]{2,}", " ", s).strip()
    return s[:limit]


def send(text, reply_to=None):
    """Telegram'a mesaj; message_id doner (state icin)."""
    import urllib.parse
    import urllib.request
    token = read(os.path.join(CONF_DIR, "telegram_token")).strip()
    chat = read(os.path.join(CONF_DIR, "telegram_chat_id")).strip()
    if not (token and chat):
        log("telegram ayarli degil; mesaj gonderilmedi")
        return None
    params = {"chat_id": chat, "text": text}
    if reply_to:
        params["reply_to_message_id"] = reply_to
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=30) as r:
        out = json.load(r)
    return (out.get("result") or {}).get("message_id")


# ---------------------------------------------------------------------------
# Soru secimi
# ---------------------------------------------------------------------------
def next_cadence_step():
    for line in read(CADENCE).splitlines():
        m = re.match(r"\s*- \[ \]\s+(.+)", line)
        if m:
            return m.group(1).strip()
    return None


def stalest_belief():
    best = None
    if not os.path.isdir(BELIEF_DIR):
        return None
    for f in sorted(os.listdir(BELIEF_DIR)):
        if not f.endswith(".md"):
            continue
        meta = fm(read(os.path.join(BELIEF_DIR, f)))
        key = meta.get("last_challenged", "0000-00-00")
        if best is None or key < best[0]:
            best = (key, f[:-3])
    return best


def newest_seed_age_days():
    newest = 0
    if os.path.isdir(IDEAS_DIR):
        for f in os.listdir(IDEAS_DIR):
            if f.endswith(".md") and f.lower() != "readme.md":
                newest = max(newest, os.path.getmtime(os.path.join(IDEAS_DIR, f)))
    return (time.time() - newest) / 86400 if newest else 999


def candidates():
    """Oncelik sirasinda (kind, target, question) listesi."""
    from calibrate import scan as calibration_scan
    due, needs_pred, _ = calibration_scan()
    out = []
    for name, rev in due:
        out.append(("grade", name,
                    f"📊 Karar notlama: [[{name}]] (review {rev}) hâlâ notlanmadı.\n"
                    "Tahmin neydi, gerçekte ne oldu, bu senin yargın hakkında ne öğretiyor? "
                    "Sesli ya da yazılı anlat."))
    for name in needs_pred:
        out.append(("predict", name,
                    f"🔮 Tahmin: [[{name}]] kararı verildi ama ölçülebilir tahmin yok.\n"
                    "Ne olmasını bekliyorsun, yüzde kaç güvenle, ne zaman kontrol edelim?"))
    step = next_cadence_step()
    if step:
        out.append(("cadence", step,
                    f"🎯 Kademe adımı: {step}\n"
                    "Bu hafta yaptın mı? Yaptıysan ne çıktı; yapmadıysan ne engelledi?"))
    b = stalest_belief()
    if b:
        out.append(("belief", b[1],
                    f"🧭 İnanç sınaması: [[{b[1]}]] en son {b[0]} tarihinde sınandı.\n"
                    "Son haftalarda bu inancı doğrulayan ya da çürüten somut bir olay oldu mu? "
                    "Bir örnek anlat; güvenin değişti mi?"))
    if newest_seed_age_days() > SEED_GAP_DAYS:
        out.append(("seed", "",
                    "🌱 Tohum: iki haftadır Thinking/Ideas'a yeni soru düşmedi.\n"
                    "Bu hafta zihninin dönüp durduğu soru ne? Bir cümle yeter, gerisini ben açarım."))
    return out


def pick_question(state):
    skipped = set(state.get("skipped", []))          # "kind:target"
    recent = state.get("recent_kinds", [])
    cands = candidates()
    if not cands:
        return None
    fresh = [c for c in cands if f"{c[0]}:{c[1]}" not in skipped]
    pool = fresh or cands
    # Ayni tur uc hafta ust uste sorulmasin; farkli tur varsa onu al.
    if len(recent) >= 2 and recent[-1] == recent[-2]:
        alt = [c for c in pool if c[0] != recent[-1]]
        pool = alt or pool
    return pool[0]


def ask(dry_run=False):
    state = load_state()
    if state.get("phase") in ("asked", "drafted"):
        age = (time.time() - state.get("asked_at", 0)) / 86400
        if age < PENDING_TTL_DAYS:
            log(f"bekleyen soru var ({state.get('kind')}, {age:.0f} gun); yeni soru sorulmadi")
            if not dry_run:
                send("⏳ Geçen haftanın sorusu hâlâ açık. Cevaplamak için o mesaja yanıt ver, "
                     "atlamak için 'geç' yaz.\n\n" + state.get("question", ""))
            return
        state.setdefault("skipped", []).append(f"{state.get('kind')}:{state.get('target')}")
        log("cevapsiz soru zaman asimina ugradi, atlandi")
    q = pick_question(state)
    if not q:
        log("sorulacak sey yok (her sey guncel)")
        return
    kind, target, question = q
    text = ("🧠 Haftalık düşünme sorusu\n\n" + question +
            "\n\nBu mesaja yanıt vererek (swipe) sesli ya da yazılı cevapla. "
            "Atlamak için 'geç'.")
    if dry_run:
        print(text)
        return
    mid = send(text)
    state.update({"phase": "asked", "kind": kind, "target": target, "question": question,
                  "asked_at": time.time(), "message_id": mid, "draft": None})
    state["recent_kinds"] = (state.get("recent_kinds", []) + [kind])[-4:]
    save_state(state)
    log(f"soru gonderildi: {kind} / {target}")


# ---------------------------------------------------------------------------
# Cevap -> taslak
# ---------------------------------------------------------------------------
def target_path(kind, target):
    if kind in ("grade", "predict"):
        return os.path.join(DEC_DIR, target + ".md")
    if kind == "belief":
        return os.path.join(BELIEF_DIR, target + ".md")
    if kind == "cadence":
        return CADENCE
    return None


SCHEMAS = {
    "grade": ('{"outcome": "ne oldu, 2-4 cümle", "lesson": "yargı hakkında tek cümle ders", '
              '"score": "hit|partial|miss"}'),
    "predict": ('{"prediction": "yanlışlanabilir tek cümle", "confidence": 70, '
                '"review": "YYYY-MM-DD"}'),
    "belief": ('{"instance": "tarihli somut olay, 2-3 cümle", "verdict": "supports|challenges|neutral", '
               '"confidence": "high|medium|low"}'),
    "cadence": '{"done": true, "note": "ne çıktı ya da ne engelledi, 1-3 cümle"}',
    "seed": ('{"title": "soru biçiminde kısa başlık", "idea": "bir paragraf", '
             '"why": "neden önemli, 1-2 cümle", "connects": ["mevcut not adı"], '
             '"question": "en keskin açık soru"}'),
}


def draft(kind, target, answer):
    note = read(target_path(kind, target)) if target_path(kind, target) else ""
    context = read(CONTEXT_FILE)[:2500]
    known = ", ".join(sorted(f[:-3] for d in (BELIEF_DIR, DEC_DIR, IDEAS_DIR)
                             if os.path.isdir(d) for f in os.listdir(d) if f.endswith(".md")))[:1500]
    prompt = f"""Sen {OWNER} için düşünme günlüğünü işleyen asistansın. {OWNER} aşağıdaki soruya
sesli/yazılı cevap verdi. Cevabı, hedef nota işlenecek yapılandırılmış alanlara çevir.

KURALLAR:
- İçerik EKLEME, yorum katma; sahibin söylediğini temizle ve alanlara yerleştir.
- Transkripsiyon hatası görünen özel isimleri bağlama göre düzelt.
- Em dash ve en dash kullanma.
- Tarih belirsizse bugünün tarihini kullan: {datetime.now().strftime('%Y-%m-%d')}.
- SADECE geçerli JSON döndür, başka hiçbir şey yazma. Şema:
{SCHEMAS[kind]}

# SORU TÜRÜ: {kind}
# HEDEF NOT ({target or 'yeni tohum'}):
{note[:6000]}

# {OWNER} CEVABI:
{answer[:4000]}

# BAĞLAM (isim düzeltmeleri ve bağlantılar için):
{context}
# MEVCUT NOT ADLARI (connects için yalnızca bunlardan seç): {known}"""
    out = run_prompt(prompt, timeout=240)
    if not out:
        return None
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def preview(kind, target, d):
    if kind == "grade":
        return (f"📊 [[{target}]] için Outcome taslağı\n\n"
                f"Sonuç ({clean(d.get('score', '?'), 10)}): {clean(d.get('outcome'))}\n"
                f"Ders: {clean(d.get('lesson'))}\n\nCalibration.md satırı da dolacak.")
    if kind == "predict":
        return (f"🔮 [[{target}]] için tahmin taslağı\n\n"
                f"Tahmin: {clean(d.get('prediction'))}\nGüven: %{d.get('confidence', '?')}\n"
                f"Review: {clean(d.get('review'), 10)}")
    if kind == "belief":
        return (f"🧭 [[{target}]] için sınama kaydı\n\n"
                f"Olay: {clean(d.get('instance'))}\nHüküm: {clean(d.get('verdict'), 12)}\n"
                f"Güven: {clean(d.get('confidence'), 8)}\nlast_challenged bugüne çekilecek.")
    if kind == "cadence":
        done = "işaretlenecek ✅" if d.get("done") else "açık kalacak"
        return f"🎯 Kademe adımı {done}\n\nNot: {clean(d.get('note'))}\n(Thinking/Daily'ye capture olarak düşer.)"
    if kind == "seed":
        return (f"🌱 Yeni tohum: {clean(d.get('title'), 120)}\n\n{clean(d.get('idea'))}\n\n"
                f"Neden: {clean(d.get('why'))}\nBağlar: {', '.join(map(str, d.get('connects') or []))[:200]}")
    return json.dumps(d, ensure_ascii=False)[:800]


# ---------------------------------------------------------------------------
# Uygula: Thinking/ altina yazim (yalnizca acik onay sonrasi)
# ---------------------------------------------------------------------------
def _replace_section_line(text, heading, pattern, replacement):
    """'## heading' altinda, sonraki '## 'e kadar, pattern'e uyan ilk satiri degistir."""
    lines = text.splitlines(keepends=True)
    in_sec = False
    for i, l in enumerate(lines):
        if l.startswith("## "):
            in_sec = l[3:].strip().startswith(heading)
            continue
        if in_sec and re.match(pattern, l.strip()):
            lines[i] = replacement
            return "".join(lines), True
    return text, False


def _update_calibration(target, **cols):
    text = read(CALIBRATION)
    if not text:
        return
    out = []
    for line in text.splitlines(keepends=True):
        if line.startswith("|") and f"[[{target}]]" in line:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            while len(cells) < 6:
                cells.append("")
            idx = {"prediction": 1, "conf": 2, "review": 3, "outcome": 4, "lesson": 5}
            for k, v in cols.items():
                if v:
                    cells[idx[k]] = v
            line = "| " + " | ".join(cells) + " |\n"
        out.append(line)
    write(CALIBRATION, "".join(out))


def apply(kind, target, d, answer):
    today = datetime.now().strftime("%Y-%m-%d")
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    touched = []
    if kind == "grade":
        path = target_path(kind, target)
        text = read(path)
        block = (f"- **Sonuç ({today}, {clean(d.get('score'), 10)}):** {clean(d.get('outcome'))}\n"
                 f"- **Ders:** {clean(d.get('lesson'))}\n")
        text, ok = _replace_section_line(text, "Outcome", r"^-\s*_Pending review\._", block)
        if not ok:
            text = text.rstrip("\n") + f"\n\n## Outcome ({today})\n{block}"
        text = set_fm(text, "graded", today)
        text = set_fm(text, "outcome_score", clean(d.get("score"), 10))
        write(path, text)
        touched.append(path)
        _update_calibration(target, outcome=clean(d.get("outcome"), 160),
                            lesson=clean(d.get("lesson"), 160))
        touched.append(CALIBRATION)
    elif kind == "predict":
        path = target_path(kind, target)
        text = read(path)
        conf = re.sub(r"[^\d]", "", str(d.get("confidence", "")))[:3] or "50"
        review = clean(d.get("review"), 10)
        if not re.match(r"\d{4}-\d{2}-\d{2}", review):
            review = (datetime.now() + timedelta(days=90)).strftime("%Y-%m-%d")
        text, ok = _replace_section_line(text, "Prediction", r"^-\s*\*\*Prediction:\*\*",
                                         f"- **Prediction:** {clean(d.get('prediction'))}\n")
        text, _ = _replace_section_line(text, "Prediction", r"^-\s*\*\*Confidence:\*\*",
                                        f"- **Confidence:** {conf}%\n")
        text, _ = _replace_section_line(text, "Prediction", r"^-\s*\*\*Review on:\*\*",
                                        f"- **Review on:** {review}\n")
        if not ok:
            text = text.rstrip("\n") + (f"\n\n## Prediction (calibration)\n- **Prediction:** "
                                        f"{clean(d.get('prediction'))}\n- **Confidence:** {conf}%\n"
                                        f"- **Review on:** {review}\n")
        text = set_fm(text, "confidence", f"{conf}%")
        text = set_fm(text, "review", review)
        write(path, text)
        touched.append(path)
        _update_calibration(target, prediction=clean(d.get("prediction"), 160),
                            conf=f"{conf}%", review=review)
        touched.append(CALIBRATION)
    elif kind == "belief":
        path = target_path(kind, target)
        text = read(path)
        verdict = {"supports": "destekledi", "challenges": "çürüttü"}.get(
            str(d.get("verdict", "")).lower(), "nötr")
        entry = f"### {today}\n- Olay: {clean(d.get('instance'))}\n- Hüküm: {verdict}\n"
        if "## Challenge Log" in text:
            text = text.rstrip("\n") + "\n\n" + entry
        else:
            text = text.rstrip("\n") + "\n\n## Challenge Log\n\n" + entry
        text = set_fm(text, "last_challenged", today)
        conf = str(d.get("confidence", "")).lower()
        if conf in ("high", "medium", "low"):
            text = set_fm(text, "confidence", conf)
        write(path, text)
        touched.append(path)
    elif kind == "cadence":
        text = read(CADENCE)
        if d.get("done"):
            lines = text.splitlines(keepends=True)
            for i, l in enumerate(lines):
                if re.match(r"\s*- \[ \]", l) and target[:40] in l:
                    lines[i] = l.replace("- [ ]", "- [x]", 1).rstrip("\n") + f" ✅ {today}\n"
                    break
            write(CADENCE, "".join(lines))
            touched.append(CADENCE)
        cap = os.path.join(DAILY_DIR, f"{stamp}-thinking.md")
        write(cap, f"# Kademe adımı: {clean(target, 120)}\n\n"
                   f"- Durum: {'yapıldı' if d.get('done') else 'yapılmadı'}\n"
                   f"- Not: {clean(d.get('note'))}\n\n#thinking #cadence\n\n---\n"
                   f"Kaynak: Telegram düşünme döngüsü, {stamp}\n")
        touched.append(cap)
    elif kind == "seed":
        title = clean(d.get("title"), 90).rstrip("?") + "?"
        fname = re.sub(r'[\\/:*"<>|]', "", title).strip() or f"Tohum {today}"
        path = os.path.join(IDEAS_DIR, fname + ".md")
        connects = [f"[[{clean(c, 80)}]]" for c in (d.get("connects") or [])][:5] or ["[[CONTEXT]]"]
        write(path, (f"---\ndate: {today}\ntype: idea\nstatus: seed\ntags: [idea, status/seed]\n"
                     f"source: Telegram düşünme döngüsü\n---\n\n# {title}\n\n## The Idea\n"
                     f"{clean(d.get('idea'), 1500)}\n\n## Why It Matters\n{clean(d.get('why'))}\n\n"
                     f"## Connects To\n" + "\n".join(f"- {c}" for c in connects) +
                     f"\n\n## Open Questions\n- {clean(d.get('question'))}\n\n## Next Action\n- marinate\n"))
        touched.append(path)
    # Loopback: soru + cevap + uygulanan taslak, wiki'de birikir.
    os.makedirs(QUERIES_DIR, exist_ok=True)
    # Kademe adimi gibi uzun/markdown'li hedefler frontmatter'a kisaltilarak girer.
    target = clean(re.sub(r"[*`\[\]]", "", target or ""), 120)
    slug = re.sub(r"[^\w\s-]", "", (target or d.get("title", "seed")), flags=re.U).strip().lower()
    slug = re.sub(r"-{2,}", "-", re.sub(r"[\s/]+", "-", slug))[:50].strip("-") or kind
    qpath = os.path.join(QUERIES_DIR, f"{today}-thinking-{kind}-{slug}.md")
    write(qpath, (f"---\nlang: tr\nsummary_en: Telegram thinking loop entry ({kind}) applied to "
                  f"{target or 'a new seed'} on {today}.\ncommand: thinking\nkind: {kind}\n"
                  f"target: \"{target}\"\ncompiled_at: {datetime.now().isoformat(timespec='seconds')}\n"
                  f"status: seed\ntags: [query, thinking, {kind}]\n---\n\n# Düşünme döngüsü: {kind}\n\n"
                  f"## Cevap (ham)\n{answer.strip()}\n\n## Uygulanan\n```json\n"
                  f"{json.dumps(d, ensure_ascii=False, indent=1)}\n```\n\nDokunulan: " +
                  ", ".join(os.path.relpath(t, VAULT) for t in touched) + "\n"))
    return touched


# ---------------------------------------------------------------------------
# telegram_capture kancasi
# ---------------------------------------------------------------------------
def _is_reply_to_us(msg, state):
    r = msg.get("reply_to_message") or {}
    return bool(state.get("message_id")) and r.get("message_id") == state.get("message_id")


def try_handle(token, msg, chat_id, transcribe, notify):
    """telegram_capture.handle_message bunu ONCE cagirir. Mesaj bu donguye aitse
    isler ve True doner (capture akisina girmez); degilse False."""
    state = load_state()
    phase = state.get("phase")
    if phase not in ("asked", "drafted"):
        return False
    text = (msg.get("text") or "").strip()
    low = text.lower()
    if phase == "drafted" and low in APPLY_WORDS:
        d, kind, target = state.get("draft") or {}, state["kind"], state["target"]
        touched = apply(kind, target, d, state.get("answer", ""))
        rel = ", ".join(os.path.relpath(t, VAULT) for t in touched)
        notify(f"✅ Uygulandı: {rel}")
        save_state({"skipped": state.get("skipped", []), "recent_kinds": state.get("recent_kinds", [])})
        log(f"uygulandi: {kind} / {target}")
        return True
    if low in CANCEL_WORDS and phase in ("asked", "drafted"):
        notify("Tamam, taslak silindi. Soru açık kalıyor; istersen yeniden cevapla ya da 'geç'.")
        state["phase"] = "asked"
        state["draft"] = None
        save_state(state)
        return True
    if low in SKIP_WORDS:
        state.setdefault("skipped", []).append(f"{state.get('kind')}:{state.get('target')}")
        save_state({"skipped": state["skipped"][-20:], "recent_kinds": state.get("recent_kinds", [])})
        notify("Atlandı. Gelecek hafta başka bir soru gelir.")
        return True
    is_reply = _is_reply_to_us(msg, state)
    is_prefixed = low.startswith(("cevap:", "cevap "))
    if not (is_reply or is_prefixed):
        return False   # siradan capture; telegram_capture devam eder
    # Cevap: sesli ya da yazili
    answer = None
    from telegram_capture import audio_file_id
    audio = audio_file_id(msg)
    if audio:
        answer = transcribe(token, audio[0])
        if not answer:
            notify("Ses transkript edilemedi; tekrar dener misin?")
            return True
    elif text:
        answer = re.sub(r"^cevap:?\s*", "", text, flags=re.I)
    if not answer:
        notify("Bu mesaj tipini cevap olarak işleyemiyorum; ses ya da yazı gönder.")
        return True
    notify("Aldım, taslağı hazırlıyorum...")
    d = draft(state["kind"], state["target"], answer)
    if not d:
        notify("Taslak üretilemedi (LLM boş döndü). Cevabın kaybolmadı; tekrar dene ya da 'geç'.")
        return True
    state.update({"phase": "drafted", "draft": d, "answer": answer})
    save_state(state)
    notify(preview(state["kind"], state["target"], d) + "\n\n'uygula' yazarsan nota işlerim; 'iptal' ile silerim.")
    log(f"taslak hazir: {state['kind']} / {state['target']}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ask", action="store_true", help="haftalik soruyu sec ve gonder")
    ap.add_argument("--dry-run", action="store_true", help="gondermeden yazdir")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--simulate", metavar="CEVAP", help="bekleyen soruya yerel cevap ver (taslak uretir, yazmaz)")
    args = ap.parse_args()
    if args.status:
        print(json.dumps(load_state(), ensure_ascii=False, indent=1))
        return
    if args.ask:
        ask(dry_run=args.dry_run)
        return
    if args.simulate:
        state = load_state()
        if state.get("phase") != "asked":
            q = pick_question(state)
            if not q:
                print("soru yok"); return
            state.update({"phase": "asked", "kind": q[0], "target": q[1], "question": q[2],
                          "asked_at": time.time(), "message_id": None})
        d = draft(state["kind"], state["target"], args.simulate)
        print(preview(state["kind"], state["target"], d) if d else "taslak uretilemedi")
        if d and not args.dry_run:
            state.update({"phase": "drafted", "draft": d, "answer": args.simulate})
            save_state(state)
        return
    ap.print_help()


if __name__ == "__main__":
    main()
