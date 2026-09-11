#!/usr/bin/env python3
"""Telegram voice/text capture channel for the brainless vault.

Polls the Telegram bot for new messages every 2 minutes (launchd,
<prefix>.brainless.telegram). Voice notes, audio files, video notes and
audio/video documents are all transcribed LOCALLY with whisper.cpp (the owner's
language from PROFILE.md, large-v3-turbo); audio never leaves this machine.
Anything the handler cannot process is logged AND answered in the chat, because
the offset advances either way and a silently dropped message is gone for good.
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
from owner_profile import OWNER, OWNER_FULL, WORKER, LANG, possessive, output_lang_directive  # noqa: E402
from transcript_filter import is_empty_transcript  # noqa: E402
from i18n import t  # noqa: E402

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
            [WHISPER, "-m", MODEL, "-l", LANG, "-f", wav, "--no-timestamps"],
            capture_output=True, text=True, timeout=600,
        )
        if r.returncode != 0:
            log(f"whisper error: {r.stderr[:200]}")
            return None
        return r.stdout.strip()


URL_RE = re.compile(r"https?://\S+")
LINKS_DIR = os.path.join(VAULT, "Inbox", "Links")


def _is_public_host(host):
    """SSRF guard: every resolved address must be global (internet); a host that
    resolves into a private/loopback/link-local/metadata range is rejected."""
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
    """Download the page and reduce it to readable text (with spiky_capture's converter)."""
    from spiky_capture import html_to_text
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return ""
    if not _is_public_host(parsed.hostname):  # SSRF: reject internal network/loopback/metadata
        log(f"Link to a private/internal address rejected: {parsed.hostname}")
        return ""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (brainless)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        ctype = resp.headers.get("Content-Type", "")
        if not any(t in ctype for t in ("text/html", "text/plain", "application/xhtml", "")):
            log(f"Non-text content skipped: {ctype}")
            return ""
        raw = resp.read(2_000_000)
    text = html_to_text(raw.decode("utf-8", "replace"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def make_link_note(url, page_text, comment):
    context = (read_file(CONTEXT_FILE) or "")[:3000]
    comment_line = f"- The note {OWNER} wrote along with the link: {comment}" if comment else ""
    prompt = f"""You are the capture assistant that files notes into the 'brainless' system for {OWNER}.
{OWNER} sent you a link; produce a clean reading note from the page content below.

RULES:
- First line: the page title (with #).
- Give the essence in 3-7 bullets; not generic talk, the page's actual claim and the important details.
- If you see a connection to {possessive()} projects, state it in one sentence and use a [[wikilink]].
- Last line: 1-3 tags if appropriate (like #reading).
- Return only the note content, write nothing else.
- {output_lang_directive()}
{comment_line}

# CURRENT CONTEXT FOR {OWNER}:
{context}

# PAGE ({url}):
{page_text[:12000]}"""
    return run_prompt(prompt, timeout=180)


def handle_link(raw_text, url_match):
    url = url_match.group(0).rstrip(").,>]")
    comment = URL_RE.sub("", raw_text).strip()
    log(f"Link received: {url}")
    try:
        page = fetch_page_text(url)
    except Exception as e:
        log(f"Page could not be fetched: {e}")
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
        fh.write(note + f"\n\n---\n{t('telegram_capture.source_label')}: {url}\n{t('telegram_capture.source_telegram_link')}, {stamp}\n")
    log(f"Note written: {path}")
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
    caption_line = f"- The caption {OWNER} wrote under the image: {caption}" if caption else ""
    prompt = f"""You are the capture assistant that files notes into the 'brainless' system for {OWNER}.
First open and inspect this image with the Read tool: {image_path}
Then produce a clean vault note from the image (it may be handwriting, a whiteboard, a document or a screenshot).

RULES:
- Transfer the text in the image as is (OCR); mark unreadable parts as {t('telegram_capture.unreadable_marker')}.
- If the image has no text, describe it in 1-2 sentences.
- Correct proper nouns to their right forms from the context.
- Use [[wikilink]] for the projects and people mentioned.
- First line: a short title (with #). Last line: 1-3 tags if appropriate (like #work).
- If there is an action, write it as a "- [ ]" task line.
- Return only the note content, write nothing else.
- {output_lang_directive()}
{caption_line}

# CURRENT CONTEXT FOR {OWNER} (for name corrections):
{context}

# IMAGE: {image_path} ({date_str})"""
    return run_prompt(prompt, timeout=240, allowed_tools=["Read"])


def make_note(raw_text, source):
    context = (read_file(CONTEXT_FILE) or "")[:4000]
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    prompt = f"""You are the capture assistant that files voice/text notes into the 'brainless' system for {OWNER}.
Turn the raw text below into a clean vault note.

RULES:
- Do NOT change the content, only clean it: drop filler words, tidy up the sentences.
- Correct proper nouns that look like transcription errors to their right forms from the context
  (example: "top table" -> "cap table", "Gremory" -> "Greymore", "o ge ka" -> "OGK").
- Use [[wikilink]] for the projects and people mentioned.
- First line: a short title (with #). Last line: 1-3 tags if appropriate (like #work).
- If there is an action, write it as a "- [ ]" task line.
- Return only the note content, write nothing else.
- {output_lang_directive()}

# CURRENT CONTEXT FOR {OWNER} (for name corrections):
{context}

# RAW TEXT ({source}, {date_str}):
{raw_text}"""
    return run_prompt(prompt, timeout=180)


# Telegram types that can carry audio. 2026-09-03: only "voice" was handled.
# A recording shared as a file from the phone or from another app arrives as
# "audio", "document" or "video_note"; those were dropped silently and, since
# the offset advanced anyway, the message was lost for good.
# ffmpeg detects the format from the content, so all go through the same transcript path.
AUDIO_KEYS = ("voice", "audio", "video_note", "video")


def audio_file_id(msg):
    """Extract a transcribable file_id from the message. -> (file_id, duration) | None"""
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
        """End silent drops: tell the sender about every message we could not process."""
        if not chat_id:
            return
        try:
            api(token, "sendMessage", {"chat_id": chat_id, "text": text})
        except Exception:
            pass

    # Thinking loop (thinking_loop.py): if a weekly question is pending and this
    # message is an answer to it or an apply/cancel/skip word, it is handled first
    # and does not enter the ordinary capture flow. On error the message continues
    # as a normal capture.
    try:
        import thinking_loop
        if thinking_loop.try_handle(token, msg, chat_id, transcribe, notify):
            return None
    except Exception as e:
        log(f"thinking_loop error, falling back to capture: {e}")

    if "text" in msg and msg["text"].startswith("/"):
        return None  # bot command, skip silently

    text = None
    source = None
    audio = audio_file_id(msg)
    if audio:
        file_id, duration = audio
        log(f"Audio received ({duration} s), transcribing...")
        text = transcribe(token, file_id)
        source = t("telegram_capture.source_voice")
        if not text:
            log("Transcript came back empty (20 MB limit or whisper error)")
            notify(t("telegram_capture.reply_transcribe_failed"))
            return None
        if is_empty_transcript(text):
            log("Meaningless transcript (silence/whisper artefact), skipped")
            notify(t("telegram_capture.reply_no_speech"))
            return None
    elif "photo" in msg:
        log("Image received, processing...")
        stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        img = fetch_photo(token, msg, stamp)
        if not img:
            return None
        caption = msg.get("caption", "")
        note = make_photo_note(img, caption) or f"# {t('telegram_capture.image_note_title')}\n\n{caption}".rstrip()
        note += f"\n\n![[{os.path.basename(img)}]]"
        path = os.path.join(CAPTURE_DIR, f"{stamp}-telegram.md")
        with open(path, "w") as fh:
            fh.write(note + f"\n\n---\n{t('telegram_capture.source_label')}: {t('telegram_capture.source_telegram_image')}, {stamp}\n")
        log(f"Note written: {path}")
        return note.splitlines()[0].lstrip("# ").strip()
    elif "text" in msg and not msg["text"].startswith("/"):
        raw = msg["text"].strip()
        m = URL_RE.search(raw)
        if m and len(URL_RE.sub("", raw).strip()) < 200:
            return handle_link(raw, m)
        text = raw
        source = t("telegram_capture.source_text")
    if not text:
        kinds = ", ".join(
            k for k in msg
            if k not in ("message_id", "from", "chat", "date", "message_thread_id")
        ) or t("telegram_capture.empty_kinds")
        log(f"Unsupported message type ignored: {kinds}")
        notify(t("telegram_capture.reply_unsupported", kinds=kinds))
        return None

    note = make_note(text, source) or f"# {t('telegram_capture.quick_note_title')}\n\n{text}"
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    os.makedirs(CAPTURE_DIR, exist_ok=True)
    path = os.path.join(CAPTURE_DIR, f"{stamp}-telegram.md")
    with open(path, "w") as fh:
        fh.write(note + f"\n\n---\n{t('telegram_capture.source_label')}: Telegram {source}, {stamp}\n")
    log(f"Note written: {path}")
    title = note.splitlines()[0].lstrip("# ").strip() if note else t("telegram_capture.note_title_fallback")
    return title


def main():
    token = read_file(TOKEN_FILE)
    if not token:
        return  # not configured yet; stay silent

    try:
        offset = int(read_file(STATE_FILE) or 0)
    except ValueError:
        offset = 0  # a corrupt offset file must not kill the poller for good
    # Launchd fires this right after wake, sometimes before DNS is up;
    # retry briefly instead of losing the whole 2-minute slot.
    updates = None
    for attempt in range(3):
        try:
            updates = api(token, "getUpdates", {"offset": offset + 1, "timeout": 0})
            break
        except Exception as e:
            if attempt == 2:
                log(f"getUpdates error (3 attempts): {e}")
                return
            time.sleep(5)

    allowed = read_file(CHAT_FILE)
    # H5 fail-closed: read_file returns None when the file is MISSING or UNREADABLE.
    # Tell the two apart. If the file exists on disk but cannot be read
    # (permission/transient error) messages are NOT processed; otherwise a foreign
    # message could hijack the whitelist. Re-adoption happens only in a setup run
    # where the operator explicitly sets BRAINLESS_TG_ALLOW_ADOPT=1.
    if allowed is None and os.path.exists(CHAT_FILE):
        log("CHAT_FILE exists but could not be read; round skipped for safety")
        touch_state(offset)
        return
    allow_adopt = os.environ.get("BRAINLESS_TG_ALLOW_ADOPT") == "1"
    for upd in updates.get("result", []):
        offset = max(offset, upd["update_id"])
        msg = upd.get("message") or {}
        chat_id = str(msg.get("chat", {}).get("id", ""))
        if not chat_id:
            # edited_message, channel_post etc: no "message". The offset advanced
            # anyway, so at least leave a trace.
            log(f"update without a message skipped: {sorted(upd)}")
            continue
        if allowed is None:
            if not allow_adopt:
                # Not set up and adoption disabled: never adopt a stranger.
                log(f"No whitelist, adoption disabled; chat {chat_id} ignored")
                continue
            os.makedirs(CONF_DIR, exist_ok=True)
            with open(CHAT_FILE, "w") as fh:
                fh.write(chat_id)
            os.chmod(CHAT_FILE, 0o600)
            allowed = chat_id
            log(f"Whitelist locked (adoption): chat {chat_id}")
            try:
                api(token, "sendMessage",
                    {"chat_id": chat_id,
                     "text": t("telegram_capture.reply_connected")})
            except Exception:
                pass
            continue
        if chat_id != allowed:
            log(f"Unauthorized chat ignored: {chat_id}")
            continue
        try:
            title = handle_message(token, msg, chat_id)
            if title:
                api(token, "sendMessage",
                    {"chat_id": chat_id, "text": t("telegram_capture.reply_saved", title=title)})
        except Exception as e:
            log(f"Message handling error: {e}")

    touch_state(offset)


if __name__ == "__main__":
    main()
