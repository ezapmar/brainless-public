#!/usr/bin/env python3
"""Telegram voice/text capture channel for the brainless vault.

Polls the Telegram bot for new messages every 2 minutes (launchd,
<prefix>.brainless.telegram). Voice notes, audio files, video notes and
audio/video documents are all transcribed LOCALLY with whisper.cpp (Turkish,
large-v3-turbo); audio never leaves this machine. Anything the handler cannot
process is logged AND answered in the chat, because the offset advances either
way and a silently dropped message is gone for good.
Claude then cleans the transcript (fixing mis-heard proper nouns against
CONTEXT.md) and the result lands in Thinking/Daily/ as a normal capture,
which the 23:00 nightly processor digests like any other note.

Security posture:
- Only the whitelisted chat id is served; everything else is ignored and
  logged. The first sender EVER becomes the whitelist (then it locks), so
  message the bot immediately after creating it.
- Bot token lives outside the vault repo in ~/.config/brainless/ (0600).
- The LLM gets embedded text only (no tools); the only writes are the
  capture file and a Telegram confirmation reply to the owner.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
sys.path.insert(0, os.path.join(VAULT, "tools"))
from llm import run_prompt
from owner_profile import OWNER, OWNER_FULL, WORKER  # noqa: E402
from transcript_filter import is_empty_transcript  # noqa: E402

CONF_DIR = os.path.expanduser("~/.config/brainless")
TOKEN_FILE = os.path.join(CONF_DIR, "telegram_token")
CHAT_FILE = os.path.join(CONF_DIR, "telegram_chat_id")
STATE_FILE = os.path.join(VAULT, ".agents", "state", "telegram_offset")
CAPTURE_DIR = os.path.join(VAULT, "Thinking", "Daily")
CONTEXT_FILE = os.path.join(VAULT, "_Agent-Context", "CONTEXT.md")
MODEL = os.path.expanduser("~/.local/share/whisper-models/ggml-large-v3-turbo-q5_0.bin")
WHISPER = next(
    (p for p in [
        shutil.which("whisper-cli"),
        "/opt/homebrew/opt/whisper-cpp/bin/whisper-cli",
        os.path.expanduser("~/build/whisper.cpp/build/bin/whisper-cli"),
    ] if p and os.path.exists(p)),
    "whisper-cli",
)
MAX_FILE_BYTES = 20 * 1024 * 1024  # Telegram bot API download cap


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def api(token, method, params=None, timeout=30):
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(params or {}).encode()
    with urllib.request.urlopen(url, data=data, timeout=timeout) as resp:
        return json.load(resp)


def read_file(path):
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return None


def touch_state(offset=None):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as fh:
        fh.write(str(offset if offset is not None else read_file(STATE_FILE) or 0))


def transcribe(token, file_id):
    """Download a Telegram voice file and transcribe it locally. -> str | None"""
    info = api(token, "getFile", {"file_id": file_id})
    tg_path = info["result"]["file_path"]
    size = info["result"].get("file_size", 0)
    if size > MAX_FILE_BYTES:
        return None
    url = f"https://api.telegram.org/file/bot{token}/{tg_path}"
    with tempfile.TemporaryDirectory() as tmp:
        raw = os.path.join(tmp, "voice.ogg")
        wav = os.path.join(tmp, "voice.wav")
        urllib.request.urlretrieve(url, raw)
        ffmpeg = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
        subprocess.run([ffmpeg, "-y", "-i", raw, "-ar", "16000", "-ac", "1", wav],
                       check=True, capture_output=True, timeout=120)
        r = subprocess.run(
            [WHISPER, "-m", MODEL, "-l", "tr", "-f", wav, "--no-timestamps"],
            capture_output=True, text=True, timeout=600,
        )
        if r.returncode != 0:
            log(f"whisper hata: {r.stderr[:200]}")
            return None
        return r.stdout.strip()


URL_RE = re.compile(r"https?://\S+")
LINKS_DIR = os.path.join(VAULT, "Inbox", "Links")


def _is_public_host(host):
    """SSRF korumasi: cozulen her adres global (internet) olmali; ozel/loopback/
    link-local/metadata araligina cozen host reddedilir."""
    import ipaddress
    import socket
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            return False
        if ip in ipaddress.ip_network("169.254.169.254/32"):  # cloud metadata
            return False
    return True


def fetch_page_text(url):
    """Sayfayi indir ve okunur metne indir (spiky_capture'daki donusturucuyle)."""
    from spiky_capture import html_to_text
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return ""
    if not _is_public_host(parsed.hostname):  # SSRF: ic ag/loopback/metadata reddi
        log(f"Ozel/dahili adrese link reddedildi: {parsed.hostname}")
        return ""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (brainless)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        ctype = resp.headers.get("Content-Type", "")
        if not any(t in ctype for t in ("text/html", "text/plain", "application/xhtml", "")):
            log(f"Metin olmayan icerik atlandi: {ctype}")
            return ""
        raw = resp.read(2_000_000)
    text = html_to_text(raw.decode("utf-8", "replace"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def make_link_note(url, page_text, comment):
    context = (read_file(CONTEXT_FILE) or "")[:3000]
    comment_line = f"- {OWNER} adlı sahibin linkle birlikte yazdığı not: {comment}" if comment else ""
    prompt = f"""Sen {OWNER} için 'brainless' sistemine not düşüren capture asistanısın.
{OWNER} sana bir link gönderdi; aşağıdaki sayfa içeriğinden temiz bir okuma notu üret.

KURALLAR:
- İlk satır: sayfanın başlığı (# ile).
- 3-7 maddede özü ver; genel geçer laf değil, sayfanın asıl iddiası ve önemli detaylar.
- {OWNER} adlı sahibin projeleriyle bir bağ görüyorsan tek cümleyle belirt ve [[wikilink]] kullan.
- Son satır: uygunsa 1-3 etiket (#reading gibi).
- Sadece not içeriğini döndür, başka hiçbir şey yazma.
{comment_line}

# {OWNER} İÇİN GÜNCEL BAĞLAM:
{context}

# SAYFA ({url}):
{page_text[:12000]}"""
    return run_prompt(prompt, timeout=180)


def handle_link(raw_text, url_match):
    url = url_match.group(0).rstrip(").,>]")
    comment = URL_RE.sub("", raw_text).strip()
    log(f"Link alındı: {url}")
    try:
        page = fetch_page_text(url)
    except Exception as e:
        log(f"Sayfa çekilemedi: {e}")
        page = ""
    note = None
    if page:
        note = make_link_note(url, page, comment)
    if not note:
        note = f"# Link\n\n{comment}".rstrip()
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    os.makedirs(LINKS_DIR, exist_ok=True)
    domain = re.sub(r"^www\.", "", urllib.parse.urlparse(url).netloc) or "link"
    path = os.path.join(LINKS_DIR, f"{stamp[:10]} {domain} {stamp[11:]}.md")
    with open(path, "w") as fh:
        fh.write(note + f"\n\n---\nKaynak: {url}\nTelegram link, {stamp}\n")
    log(f"Not yazıldı: {path}")
    return note.splitlines()[0].lstrip("# ").strip()


def fetch_photo(token, msg, stamp):
    """Download the largest size of a Telegram photo into CAPTURE_DIR. -> path | None"""
    sizes = msg.get("photo") or []
    if not sizes:
        return None
    info = api(token, "getFile", {"file_id": sizes[-1]["file_id"]})
    if info["result"].get("file_size", 0) > MAX_FILE_BYTES:
        return None
    tg_path = info["result"]["file_path"]
    ext = os.path.splitext(tg_path)[1] or ".jpg"
    os.makedirs(CAPTURE_DIR, exist_ok=True)
    dest = os.path.join(CAPTURE_DIR, f"{stamp}-telegram{ext}")
    urllib.request.urlretrieve(f"https://api.telegram.org/file/bot{token}/{tg_path}", dest)
    return dest


def make_photo_note(image_path, caption):
    context = (read_file(CONTEXT_FILE) or "")[:4000]
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    caption_line = f"- {OWNER} adlı sahibin görsel altına yazdığı not: {caption}" if caption else ""
    prompt = f"""Sen {OWNER} için 'brainless' sistemine not düşüren capture asistanısın.
Önce Read aracıyla şu görseli aç ve incele: {image_path}
Sonra görselden temiz bir vault notu üret (el yazısı, whiteboard, belge veya ekran görüntüsü olabilir).

KURALLAR:
- Görseldeki metni olduğu gibi aktar (OCR); okunamayan yerleri [okunamadı] diye işaretle.
- Görselde metin yoksa 1-2 cümleyle betimle.
- Özel isimleri bağlamdaki doğru halleriyle düzelt.
- Bahsedilen proje ve kişiler için [[wikilink]] kullan.
- İlk satır: kısa başlık (# ile). Son satır: uygunsa 1-3 etiket (#work gibi).
- Aksiyon varsa "- [ ]" görev satırı olarak yaz.
- Sadece not içeriğini döndür, başka hiçbir şey yazma.
{caption_line}

# {OWNER} İÇİN GÜNCEL BAĞLAM (isim düzeltmeleri için):
{context}

# GÖRSEL: {image_path} ({date_str})"""
    return run_prompt(prompt, timeout=240, allowed_tools=["Read"])


def make_note(raw_text, source):
    context = (read_file(CONTEXT_FILE) or "")[:4000]
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    prompt = f"""Sen {OWNER} için 'brainless' sistemine sesli/yazılı not düşüren capture asistanısın.
Aşağıdaki ham metni temiz bir vault notuna çevir.

KURALLAR:
- İçeriği DEĞİŞTİRME, sadece temizle: dolgu sözcüklerini at, cümleleri toparla.
- Transkripsiyon hatası görünen özel isimleri bağlamdaki doğru halleriyle düzelt
  (örnek: "top table" -> "cap table", "Gremory" -> "Greymore", "o ge ka" -> "OGK").
- Bahsedilen proje ve kişiler için [[wikilink]] kullan.
- İlk satır: kısa başlık (# ile). Son satır: uygunsa 1-3 etiket (#work gibi).
- Aksiyon varsa "- [ ]" görev satırı olarak yaz.
- Sadece not içeriğini döndür, başka hiçbir şey yazma.

# {OWNER} İÇİN GÜNCEL BAĞLAM (isim düzeltmeleri için):
{context}

# HAM METİN ({source}, {date_str}):
{raw_text}"""
    return run_prompt(prompt, timeout=180)


# Ses tasiyabilecek Telegram tipleri. 2026-09-03: yalnizca "voice" isleniyordu.
# Telefondan dosya olarak ya da baska bir uygulamadan paylasilan kayit "audio",
# "document" ya da "video_note" olarak gelir; bunlar sessizce dusuyordu ve
# offset yine de ilerledigi icin mesaj kalici olarak kayboluyordu.
# ffmpeg formati icerikten tanidigi icin hepsi ayni transkript yolundan gecer.
AUDIO_KEYS = ("voice", "audio", "video_note", "video")


def audio_file_id(msg):
    """Mesajdan transkript edilebilir file_id cikar. -> (file_id, sure) | None"""
    for key in AUDIO_KEYS:
        part = msg.get(key)
        if isinstance(part, dict) and part.get("file_id"):
            return part["file_id"], part.get("duration", "?")
    doc = msg.get("document")
    if isinstance(doc, dict) and str(doc.get("mime_type", "")).startswith(("audio/", "video/")):
        return doc.get("file_id"), "?"
    return None


def handle_message(token, msg, chat_id=None):
    def notify(text):
        """Sessiz dusmeyi bitir: isleyemedigimiz her mesaji gonderene soyle."""
        if not chat_id:
            return
        try:
            api(token, "sendMessage", {"chat_id": chat_id, "text": text})
        except Exception:
            pass

    # Dusunme dongusu (thinking_loop.py): bekleyen bir haftalik soru varsa ve bu
    # mesaj ona yanit ya da "uygula/iptal/gec" ise once o isler; siradan capture
    # akisina girmez. Hata olursa mesaj normal capture olarak devam eder.
    try:
        import thinking_loop
        if thinking_loop.try_handle(token, msg, chat_id, transcribe, notify):
            return None
    except Exception as e:
        log(f"thinking_loop hata, capture'a dusuluyor: {e}")

    if "text" in msg and msg["text"].startswith("/"):
        return None  # bot komutu, sessizce gec

    text = None
    source = None
    audio = audio_file_id(msg)
    if audio:
        file_id, duration = audio
        log(f"Ses alındı ({duration} sn), transkript ediliyor...")
        text = transcribe(token, file_id)
        source = "sesli not"
        if not text:
            log("Transkript bos dondu (20 MB siniri ya da whisper hatasi)")
            notify("Ses alındı ama transkript edilemedi. Muhtemel sebep: 20 MB "
                   "indirme sınırı ya da whisper hatası. Kayıt vault'a düşmedi.")
            return None
        if is_empty_transcript(text):
            log("Anlamsiz transkript (sessizlik/whisper artefakti), atlandi")
            notify("Ses alındı ama konuşma algılanmadı (sessizlik ya da whisper "
                   "artefaktı). Kayıt vault'a düşmedi.")
            return None
    elif "photo" in msg:
        log("Görsel alındı, işleniyor...")
        stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        img = fetch_photo(token, msg, stamp)
        if not img:
            return None
        caption = msg.get("caption", "")
        note = make_photo_note(img, caption) or f"# Görsel Not\n\n{caption}".rstrip()
        note += f"\n\n![[{os.path.basename(img)}]]"
        path = os.path.join(CAPTURE_DIR, f"{stamp}-telegram.md")
        with open(path, "w") as fh:
            fh.write(note + f"\n\n---\nKaynak: Telegram görsel, {stamp}\n")
        log(f"Not yazıldı: {path}")
        return note.splitlines()[0].lstrip("# ").strip()
    elif "text" in msg and not msg["text"].startswith("/"):
        raw = msg["text"].strip()
        m = URL_RE.search(raw)
        if m and len(URL_RE.sub("", raw).strip()) < 200:
            return handle_link(raw, m)
        text = raw
        source = "yazılı not"
    if not text:
        kinds = ", ".join(
            k for k in msg
            if k not in ("message_id", "from", "chat", "date", "message_thread_id")
        ) or "bos"
        log(f"Desteklenmeyen mesaj tipi yoksayildi: {kinds}")
        notify(f"Bu mesaj tipi işlenemiyor ({kinds}), vault'a düşmedi. "
               "Sesli not, ses dosyası, fotoğraf ya da yazı gönderebilirsin.")
        return None

    note = make_note(text, source) or f"# Hızlı Not\n\n{text}"
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    os.makedirs(CAPTURE_DIR, exist_ok=True)
    path = os.path.join(CAPTURE_DIR, f"{stamp}-telegram.md")
    with open(path, "w") as fh:
        fh.write(note + f"\n\n---\nKaynak: Telegram {source}, {stamp}\n")
    log(f"Not yazıldı: {path}")
    title = note.splitlines()[0].lstrip("# ").strip() if note else "Not"
    return title


def main():
    token = read_file(TOKEN_FILE)
    if not token:
        return  # not configured yet; stay silent

    try:
        offset = int(read_file(STATE_FILE) or 0)
    except ValueError:
        offset = 0  # bozuk offset dosyasi poller'i kalici olarak oldurmesin
    # Launchd fires this right after wake, sometimes before DNS is up;
    # retry briefly instead of losing the whole 2-minute slot.
    updates = None
    for attempt in range(3):
        try:
            updates = api(token, "getUpdates", {"offset": offset + 1, "timeout": 0})
            break
        except Exception as e:
            if attempt == 2:
                log(f"getUpdates hata (3 deneme): {e}")
                return
            time.sleep(5)

    allowed = read_file(CHAT_FILE)
    # H5 fail-closed: read_file, dosya YOK ya da OKUNAMAZ ise None doner. Ikisini
    # ayir. Dosya diskte varsa ama okunamiyorsa (izin/gecici hata) mesaj ISLENMEZ;
    # yoksa yabanci bir mesaj whitelist'i kaciririr. Yeniden sahiplenme yalnizca
    # operatorun acikca BRAINLESS_TG_ALLOW_ADOPT=1 verdigi kurulum turunda olur.
    if allowed is None and os.path.exists(CHAT_FILE):
        log("CHAT_FILE var ama okunamadi; guvenlik geregi tur atlandi")
        touch_state(offset)
        return
    allow_adopt = os.environ.get("BRAINLESS_TG_ALLOW_ADOPT") == "1"
    for upd in updates.get("result", []):
        offset = max(offset, upd["update_id"])
        msg = upd.get("message") or {}
        chat_id = str(msg.get("chat", {}).get("id", ""))
        if not chat_id:
            # edited_message, channel_post vb: "message" yok. Offset yine de
            # ilerledi, en azindan iz birak.
            log(f"message tasimayan update atlandi: {sorted(upd)}")
            continue
        if allowed is None:
            if not allow_adopt:
                # Kurulmamis ve adoption kapali: hicbir yabanciyi sahiplendirme.
                log(f"Whitelist yok, adoption kapali; chat {chat_id} yoksayildi")
                continue
            os.makedirs(CONF_DIR, exist_ok=True)
            with open(CHAT_FILE, "w") as fh:
                fh.write(chat_id)
            os.chmod(CHAT_FILE, 0o600)
            allowed = chat_id
            log(f"Whitelist kilitlendi (adoption): chat {chat_id}")
            try:
                api(token, "sendMessage",
                    {"chat_id": chat_id,
                     "text": "Bağlandık. Sesli veya yazılı not at, vault'a düşeyim. 🧠"})
            except Exception:
                pass
            continue
        if chat_id != allowed:
            log(f"Yetkisiz chat yoksayıldı: {chat_id}")
            continue
        try:
            title = handle_message(token, msg, chat_id)
            if title:
                api(token, "sendMessage",
                    {"chat_id": chat_id, "text": f"Vault'a düştü: {title}"})
        except Exception as e:
            log(f"Mesaj işleme hatası: {e}")

    touch_state(offset)


if __name__ == "__main__":
    main()
