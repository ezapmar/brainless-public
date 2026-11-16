#!/usr/bin/env python3
"""The one Telegram poller: capture into the vault, plus owner replies.

Owner text, images and audio become vault notes. Two kinds of message are not
notes: a reply to one of the bot's own messages (an approval or a follow-up)
and a message starting with "?" (a question to the vault). Those go to the
outbox `incoming` table and tools/interactions.py answers them in the same run.
Receipts and errors go out through tools/notify.py. Raw updates remain in a
local journal until processing succeeds.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
from llm import run_prompt
from owner_profile import OWNER, LANG, possessive, output_lang_directive
from transcript_filter import is_empty_transcript
from i18n import t
from fsutil import atomic_write

CONF_DIR = os.path.expanduser("~/.config/brainless")
TOKEN_FILE = os.path.join(CONF_DIR, "telegram_token")
CHAT_FILE = os.path.join(CONF_DIR, "telegram_chat_id")
STATE_FILE = os.path.join(VAULT, ".agents", "state", "telegram_offset")
CAPTURE_DIR = os.path.join(VAULT, "Thinking", "Daily")
CONTEXT_FILE = os.path.join(VAULT, "_Agent-Context", "CONTEXT.md")
# Owner-kept spelling list for voice notes: one term per line, plus known
# mishearings as "wrong -> right". Until 30/10/2026 the cleanup step saw only
# the first 4000 characters of CONTEXT.md, so everyday words (the company name,
# "payroll", the solicitor's name) came out misheard. Lives in the vault, never
# in code, because it is mostly names.
GLOSSARY_FILE = os.path.join(VAULT, "_Agent-Context", "capture-glossary.md")
WHISPER_PROMPT_CHARS = 800  # whisper caps the initial prompt at n_text_ctx/2 tokens
MODEL =os.path.expanduser("~/.local/share/whisper-models/ggml-large-v3-turbo-q5_0.bin")
WHISPER = next(
    (p for p in [
        shutil.which("whisper-cli"),
        "/opt/homebrew/opt/whisper-cpp/bin/whisper-cli",
        os.path.expanduser("~/build/whisper.cpp/build/bin/whisper-cli"),
    ] if p and os.path.exists(p)),
    "whisper-cli",
)
MAX_FILE_BYTES = 20 * 1024 * 1024  # Telegram bot API download cap

# Wait after the 1st, 2nd, ... failed attempt at a journalled message. The first
# retry is the next run: most failures are a blip (DNS right after wake, a busy
# model). After that the same ladder smart_processor uses for a failing file.
# Until 08/10/2026 nothing counted attempts, so a message whose handler always
# failed (corrupt audio, a refusal) was downloaded and transcribed again every
# two minutes for good. A failure after the last wait parks the message.
# Same ladder smart_processor.py uses for a failing file.
BACKOFF_SCHEDULE = (3600, 4 * 3600, 12 * 3600, 24 * 3600, 3 * 24 * 3600)
RETRY_DELAYS = (0, *BACKOFF_SCHEDULE)
MAX_ATTEMPTS = len(RETRY_DELAYS) + 1


from logline import log


def api(token, method, params=None, timeout=30):
    # Sending goes through tools/notify.py (durable outbox); this side only reads.
    if method not in {"getUpdates", "getFile"}:
        raise ValueError("Telegram polling is read-only")
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(params or {}).encode()
    with urllib.request.urlopen(url, data=data, timeout=timeout) as resp:
        return json.load(resp)


WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")


def plain_title(title):
    """[[Page|alias]] -> alias, [[Page]] -> Page: receipts are read, not linked."""
    return WIKILINK_RE.sub(lambda m: m.group(2) or m.group(1), title).strip()


def read_file(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


def touch_state(offset=None):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        fh.write(str(offset if offset is not None else read_file(STATE_FILE) or 0))


def load_glossary(path=None):
    """Read the capture glossary. -> (terms, corrections)

    terms: correct spellings, in file order. corrections: (wrong, right) pairs;
    their right side also counts as a term. Headings, blank lines and lines
    starting with '>' are skipped; a leading '- ' is allowed."""
    text = read_file(path or GLOSSARY_FILE) or ""
    terms, corrections = [], []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("- "):
            line = line[2:].strip()
        if not line or line.startswith(("#", ">")):
            continue
        if "->" in line:
            wrong, right = (part.strip() for part in line.split("->", 1))
            if wrong and right:
                corrections.append((wrong, right))
                line = right
            else:
                continue
        if line not in terms:
            terms.append(line)
    return terms, corrections


def whisper_prompt(terms):
    """Comma-joined terms that fit whisper's initial prompt. -> str"""
    out = ""
    for term in terms:
        nxt = f"{out}, {term}" if out else term
        if len(nxt) > WHISPER_PROMPT_CHARS:
            break
        out = nxt
    return out


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
        cmd = [WHISPER, "-m", MODEL, "-l", LANG, "-f", wav, "--no-timestamps"]
        prompt = whisper_prompt(load_glossary()[0])
        if prompt:
            cmd += ["--prompt", prompt]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            log(f"whisper error: {r.stderr[:200]}")
            return None
        return r.stdout.strip()


URL_RE = re.compile(r"https?://[^\s<>]+")
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


def _public_url(url):
    """The reason a URL may not be fetched, or None when it may: http(s) only,
    and every address its host resolves to is on the public internet."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "not an http(s) link"
    if not _is_public_host(parsed.hostname):
        return f"private/internal address: {parsed.hostname}"
    return None


class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
    """Re-run the SSRF check on every hop. The first check covered the link the
    owner sent; without this a public page could 3xx to the LAN or to the
    cloud metadata address and urlopen would follow it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        reason = _public_url(urllib.parse.urljoin(req.full_url, newurl))
        if reason:
            raise urllib.error.HTTPError(newurl, code, f"redirect refused ({reason})", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_TEXT_TYPES = ("text/html", "text/plain", "application/xhtml")


def fetch_page_text(url):
    """Download the page and reduce it to readable text (with spiky_capture's converter)."""
    from spiky_capture import html_to_text
    reason = _public_url(url)
    if reason:
        if "private" in reason:
            log(f"Link to a private/internal address rejected: {urllib.parse.urlparse(url).hostname}")
        return ""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (brainless)"})
    opener = urllib.request.build_opener(_GuardedRedirect())
    with opener.open(req, timeout=30) as resp:
        ctype = resp.headers.get("Content-Type", "")
        if not any(t in ctype for t in _TEXT_TYPES):
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
    return run_prompt(prompt, timeout=180, lane="capture-link")


def handle_link(raw_text, url_match, stamp=None):
    url = url_match.group(0).rstrip(").,>]")
    # Some clients autolink as <url>; drop the empty brackets the URL leaves behind.
    comment = re.sub(r"<\s*>", "", URL_RE.sub("", raw_text)).strip()
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
    stamp = stamp or datetime.now().strftime("%Y-%m-%d-%H%M%S")
    os.makedirs(LINKS_DIR, exist_ok=True)
    domain = re.sub(r"^www\.", "", urllib.parse.urlparse(url).netloc) or "link"
    path = os.path.join(LINKS_DIR, f"{stamp[:10]} {domain} {stamp[11:]}.md")
    atomic_write(path, note + f"\n\n---\n{t('telegram_capture.source_label')}: {url}\n{t('telegram_capture.source_telegram_link')}, {stamp}\n")
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
Inspect this image (it is attached; if it is not, open it with the Read tool): {image_path}
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

# GLOSSARY (correct spellings and known mishearings):
{glossary_block()}

# CURRENT CONTEXT FOR {OWNER} (for name corrections):
{context}

# IMAGE: {image_path} ({date_str})"""
    return run_prompt(prompt, timeout=240, allowed_tools=["Read"], lane="capture-photo",
                      images=[image_path])


def raw_transcript_block(text):
    """Whisper's own words as a folded callout under a voice note. Until
    30/10/2026 only the cleaned note was kept, so a misheard name could not be
    checked against what was actually said. -> str"""
    body = "\n".join(f"> {line}" if line else ">" for line in text.strip().splitlines())
    return f"> [!quote]- {t('telegram_capture.raw_transcript_label')}\n{body}"


def glossary_block():
    """The glossary as prompt lines for the cleanup step. -> str"""
    terms, corrections = load_glossary()
    lines = [f"- {term}" for term in terms]
    lines += [f'- "{wrong}" is a mishearing of "{right}"' for wrong, right in corrections]
    return "\n".join(lines) or "(none)"


def make_note(raw_text, source):
    context = (read_file(CONTEXT_FILE) or "")[:4000]
    glossary = glossary_block()
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    prompt = f"""You are the capture assistant that files voice/text notes into the 'brainless' system for {OWNER}.
Turn the raw text below into a clean vault note.

RULES:
- Do NOT change the content, only clean it: drop filler words, tidy up the sentences.
- Correct proper nouns that look like transcription errors to their right forms from the context
  (example: "top table" -> "cap table", "ey pi ay" -> "API").
  The glossary below is the owner's own spelling list: when a word sounds like one of its
  terms, write the term. Leave a word you cannot place as it is rather than guess.
- Use [[wikilink]] for the projects and people mentioned.
- First line: a short title (with #). Last line: 1-3 tags if appropriate (like #work).
- If there is an action, write it as a "- [ ]" task line.
- Return only the note content, write nothing else.
- {output_lang_directive()}

# GLOSSARY (correct spellings and known mishearings):
{glossary}

# CURRENT CONTEXT FOR {OWNER} (for name corrections):
{context}

# RAW TEXT ({source}, {date_str}):
{raw_text}"""
    return run_prompt(prompt, timeout=180, lane="capture-note")


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


def private_sender(msg):
    """True for a one-to-one chat whose sender is the chat itself.

    The whitelist is a chat id. In a group every member shares that id, so a
    chat id alone would let anyone in the group capture notes and approve
    writes. A private chat with the bot has chat.id == from.id."""
    chat = msg.get("chat") or {}
    sender = msg.get("from") or {}
    return (chat.get("type") == "private" and not sender.get("is_bot")
            and str(sender.get("id", "")) == str(chat.get("id", "")) != "")


def as_reply(token, msg):
    """An owner reply or question in the outbox message shape, or None for a note.

    A reply to one of the bot's messages carries an approval or a follow-up; a
    text starting with "?" is a question to the vault. Voice replies are
    transcribed here so the handlers only ever see text."""
    reply_to = msg.get("reply_to_message") or {}
    replied = reply_to.get("from") or {}
    bot_id = (token or "").split(":", 1)[0]
    is_reply = bool(replied.get("is_bot")) and (not bot_id or str(replied.get("id", "")) == bot_id)
    text = (msg.get("text") or msg.get("caption") or "").strip()
    is_question = text.startswith("?")
    if not (is_reply or is_question):
        return None
    if not text and audio_file_id(msg):
        text = transcribe(token, audio_file_id(msg)[0]) or ""
        if not text:
            raise RuntimeError("Reply transcription failed")
    if is_question:
        text = text[1:].strip()
    from notify import tg_event
    return {"id": tg_event(msg["message_id"]), "content": text,
            "pubkey": str((msg.get("from") or {}).get("id", "")),
            "created_at": msg.get("date") or int(time.time()),
            "parent": tg_event(reply_to["message_id"]) if is_reply else None}


def handle_message(token, msg, chat_id=None):
    from notify import send
    reply = as_reply(token, msg)
    if reply is not None:
        if reply["content"]:
            from notify import Outbox
            channel = Outbox().add_reply(reply)
            log(f"Reply queued for #{channel}")
        return None  # not a note: no "saved" receipt
    mid = str(msg.get("message_id", "unknown"))
    event = f"telegram:{chat_id}:{mid}"
    stamp = datetime.fromtimestamp(msg.get("date") or time.time()).strftime("%Y-%m-%d-%H%M%S") + "-" + mid

    def notify(text):
        send("inbox", text, key=event + ":notice:" + __import__('hashlib').sha256(text.encode()).hexdigest())

    if (msg.get("text") or "").startswith("/"):
        notify("Komut yok. Not için yaz ya da konuş; vault'a soru için mesaja ? ile başla; onay için ilgili mesajı yanıtla.")
        return None

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
            raise RuntimeError("Transcription failed")
        if is_empty_transcript(text):
            log("Meaningless transcript (silence/whisper artefact), skipped")
            notify(t("telegram_capture.reply_no_speech"))
            return None
    elif "photo" in msg:
        log("Image received, processing...")
        img = fetch_photo(token, msg, stamp)
        if not img:
            raise RuntimeError("Image download failed")
        caption = msg.get("caption", "")
        note = make_photo_note(img, caption) or f"# {t('telegram_capture.image_note_title')}\n\n{caption}".rstrip()
        note += f"\n\n![[{os.path.basename(img)}]]"
        path = os.path.join(CAPTURE_DIR, f"{stamp}-telegram.md")
        atomic_write(path, note + f"\n\n---\n{t('telegram_capture.source_label')}: {t('telegram_capture.source_telegram_image')}, {stamp}\n")
        log(f"Note written: {path}")
        return note.splitlines()[0].lstrip("# ").strip()
    elif "text" in msg and not msg["text"].startswith("/"):
        raw = msg["text"].strip()
        m = URL_RE.search(raw)
        if m and len(URL_RE.sub("", raw).strip()) < 200:
            return handle_link(raw, m, stamp=stamp)
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
    if audio:
        note += "\n\n" + raw_transcript_block(text)
    os.makedirs(CAPTURE_DIR, exist_ok=True)
    path = os.path.join(CAPTURE_DIR, f"{stamp}-telegram.md")
    atomic_write(path, note + f"\n\n---\n{t('telegram_capture.source_label')}: Telegram {source}, {stamp}\n")
    log(f"Note written: {path}")
    title = note.splitlines()[0].lstrip("# ").strip() if note else t("telegram_capture.note_title_fallback")
    return title


def retry_delay(attempts):
    """Seconds to wait after `attempts` failed attempts before the next one."""
    if attempts <= 0:
        return 0
    return RETRY_DELAYS[min(attempts, len(RETRY_DELAYS)) - 1]


def _save_record(pending, record):
    atomic_write(pending, json.dumps(record, ensure_ascii=False))


def _notice(text, key):
    """Best-effort notice: a broken outbox must not stop the other records."""
    from notify import send
    try:
        send("inbox", text, key=key)
    except Exception as exc:
        log(f"Notice failed: {type(exc).__name__}")


def _record_failure(pending, record, exc, token, now):
    """Count a failed attempt in the record; park it after the last one.

    The error text stays in the owner-only record with the bot token cut out;
    the log and the chat only ever see the exception type."""
    attempts = int(record.get("attempts", 0)) + 1
    error = f"{type(exc).__name__}: {exc}"
    if token:
        error = error.replace(token, "<token>")
    record.update(attempts=attempts, last_attempt=int(now), last_error=error[:300])
    log(f"Message handling error: {type(exc).__name__} (attempt {attempts}/{MAX_ATTEMPTS})")
    if attempts >= MAX_ATTEMPTS:
        # Written before the notice: a crash in between costs the notice, never
        # a second one, and a parked record is never handled or announced again.
        record["state"] = "parked"
        _save_record(pending, record)
        _notice(f"Telegram kaydı {attempts} denemede işlenemedi ve park edildi "
                f"({type(exc).__name__}). Girdi .agents/state/telegram_pending/{pending.name} "
                "içinde duruyor; yeniden denemek için state alanını sil.",
                key="telegram:parked:" + pending.stem)
        return
    _save_record(pending, record)
    _notice("Telegram kaydı işlenemedi; girdi saklandı, yeniden denenecek.",
            key="telegram:failure:" + pending.stem)


def retry_pending(token, allowed, now=None):
    from pathlib import Path
    from notify import send
    now = time.time() if now is None else now
    for pending in sorted((Path(STATE_FILE).parent / "telegram_pending").glob("*.json")):
        try:
            record = json.loads(pending.read_text(encoding="utf-8"))
            chat, msg = record["chat"], record["message"]
            if chat != allowed:
                continue
            if record.get("state") == "parked":
                continue
            key = f"telegram:{chat}:{msg['message_id']}"
            # Persist the result before network delivery so retries do not rerun
            # successful OCR/transcription or change the receipt body.
            if "result" not in record:
                attempts = int(record.get("attempts", 0))
                if now - record.get("last_attempt", 0) < retry_delay(attempts):
                    continue
                try:
                    record["result"] = handle_message(token, msg, chat)
                except Exception as exc:
                    _record_failure(pending, record, exc, token, now)
                    continue
                _save_record(pending, record)
            if record["result"]:
                stamp = datetime.fromtimestamp(msg.get("date") or time.time()).strftime("%Y-%m-%d-%H%M%S") + "-" + str(msg['message_id'])
                files = [str(p.relative_to(VAULT)) for base in (CAPTURE_DIR, LINKS_DIR)
                         for p in Path(base).glob(stamp + "*.md")]
                receipt = t("telegram_capture.reply_saved", title=plain_title(record["result"]))
                send("inbox", "\n".join([receipt] + files), key=key + ":saved")
            pending.unlink()
        except Exception as exc:
            # Delivery after a stored result, or an unreadable record: cheap to
            # retry, so it is retried every run without counting attempts.
            log(f"Message handling error: {type(exc).__name__}")
            _notice("Telegram kaydı işlenemedi; girdi saklandı, yeniden denenecek.",
                    key="telegram:failure:" + pending.stem)


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
            updates = api(token, "getUpdates", {"offset": offset + 1, "timeout": 0,
                          "allowed_updates": json.dumps(["message"])})
            break
        except Exception as e:
            if attempt == 2:
                # The request URL carries the token and some exceptions
                # (InvalidURL, HTTPError) echo it; log the type only.
                log(f"getUpdates error (3 attempts): {type(e).__name__}")
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
        if not private_sender(msg):
            # A group or channel shares one chat id among many people; only a
            # one-to-one chat with the owner may write to the vault or approve.
            log(f"Non-private chat or foreign sender ignored: {chat_id}")
            continue
        if allowed is None:
            if not allow_adopt:
                # Not set up and adoption disabled: never adopt a stranger.
                log(f"No whitelist, adoption disabled; chat {chat_id} ignored")
                continue
            os.makedirs(CONF_DIR, exist_ok=True)
            with open(CHAT_FILE, "w", encoding="utf-8") as fh:
                fh.write(chat_id)
            os.chmod(CHAT_FILE, 0o600)
            allowed = chat_id
            log(f"Whitelist locked (adoption): chat {chat_id}")
            from notify import send
            send("inbox", t("telegram_capture.reply_connected"), key=f"telegram:connected:{chat_id}")
            continue
        if chat_id != allowed:
            log(f"Unauthorized chat ignored: {chat_id}")
            continue
        # Keep raw input until processing succeeds. Retry failures even after
        # getUpdates advances, and use deterministic filenames on replay.
        from pathlib import Path
        from notify import send
        journal = Path(STATE_FILE).parent / "telegram_pending"
        journal.mkdir(parents=True, exist_ok=True)
        pending = journal / f"{upd['update_id']}.json"
        if not pending.exists():
            msg.setdefault("date", int(time.time()))
            atomic_write(pending, json.dumps({"chat": chat_id, "message": msg}, ensure_ascii=False))
            pending.chmod(0o600)

    # All authorised updates are durable before advancing the polling offset.
    touch_state(offset)
    retry_pending(token, allowed)
    # Answer queued replies and retry undelivered notices in the same tick.
    try:
        import interactions
        interactions.main()
    except Exception as exc:
        log(f"Reply handling failed: {type(exc).__name__}")


if __name__ == "__main__":
    main()
