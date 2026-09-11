#!/usr/bin/env python3
"""Buzz capture channel worker (worker, every 2 minutes).

Twin of telegram_capture.py for the Buzz #inbox channel. Reads new messages
posted by the owner, turns voice notes (local whisper), images (Claude vision),
links and plain text into vault notes under Thinking/Daily, then replies in the
thread with the note title and path. The Buzz identity "Inbox" does the talking.

Only messages from allowed authors are processed (owner by default; extra hex
pubkeys can be listed one per line in ~/.config/brainless/buzz/capture_authors).
State: .agents/state/buzz_capture_seen (processed event ids) and
.agents/state/buzz_capture_since (unix time of the newest processed message).
Never raises to the caller: every failure is logged and, where possible,
reported back into the thread so nothing drops silently.
"""
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import telegram_capture as tc  # noqa: E402  (reuses whisper, prompts, paths)
from owner_profile import OWNER, OWNER_FULL, WORKER, LANG  # noqa: E402
from i18n import t  # noqa: E402

VAULT = tc.VAULT
BUZZ_DIR = os.path.expanduser("~/.config/brainless/buzz")
BUZZ_BIN = os.path.expanduser("~/.cargo/bin/buzz")
STATE_SEEN = os.path.join(VAULT, ".agents", "state", "buzz_capture_seen")
STATE_SINCE = os.path.join(VAULT, ".agents", "state", "buzz_capture_since")
def _owner_pubkey():
    """Owner pubkey: env, then ~/.config/brainless/buzz/owner_pubkey, then any agent env file."""
    v = os.environ.get("BUZZ_OWNER_PUBKEY", "").strip()
    if len(v) == 64:
        return v
    for name in ("owner_pubkey",):
        v = (tc.read_file(os.path.join(BUZZ_DIR, name)) or "").strip()
        if len(v) == 64:
            return v
    for env_file in sorted(glob.glob(os.path.join(BUZZ_DIR, "*.env"))):
        for line in (tc.read_file(env_file) or "").splitlines():
            if line.startswith("BUZZ_ACP_AGENT_OWNER="):
                v = line.split("=", 1)[1].strip()
                if len(v) == 64:
                    return v
    return ""


OWNER = _owner_pubkey()
CHANNEL_NAME = "inbox"
IDENTITY = "inbox"
AUDIO_EXT = {".m4a", ".mp3", ".ogg", ".oga", ".opus", ".wav", ".webm", ".mp4", ".aac", ".flac", ".caf"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif", ".tif", ".tiff"}
MEDIA_RE = re.compile(r"https?://[^\s)\]]+/media/[A-Za-z0-9._-]+")
MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")

log = tc.log


def relay_url():
    p = os.path.join(BUZZ_DIR, "relay_url")
    return (os.environ.get("BUZZ_RELAY_URL") or tc.read_file(p) or "http://localhost:3000").strip()


def secret(identity):
    text = tc.read_file(os.path.join(BUZZ_DIR, "keys", identity)) or ""
    for line in text.splitlines():
        if line.startswith("Secret key:"):
            return line.split(":", 1)[1].strip()
    return None


def channel_id(name):
    try:
        with open(os.path.join(BUZZ_DIR, "channels.json")) as fh:
            return json.load(fh).get(name)
    except (OSError, ValueError):
        return None


def buzz(args, identity=IDENTITY, stdin=None, timeout=60):
    env = dict(os.environ, BUZZ_RELAY_URL=relay_url(), BUZZ_PRIVATE_KEY=secret(identity) or "")
    r = subprocess.run([BUZZ_BIN, *args], input=stdin, capture_output=True, text=True,
                       timeout=timeout, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"buzz {' '.join(args[:2])} rc={r.returncode}: {r.stderr[:200]}")
    return r.stdout


def allowed_authors():
    authors = {OWNER}
    extra = tc.read_file(os.path.join(BUZZ_DIR, "capture_authors")) or ""
    for line in extra.splitlines():
        line = line.strip()
        if len(line) == 64:
            authors.add(line)
    return authors


def load_seen():
    text = tc.read_file(STATE_SEEN) or ""
    return set(text.split())


def save_seen(seen):
    os.makedirs(os.path.dirname(STATE_SEEN), exist_ok=True)
    with open(STATE_SEEN, "w") as fh:
        fh.write("\n".join(list(seen)[-500:]))


def load_since():
    v = tc.read_file(STATE_SINCE)
    if v and v.isdigit():
        return int(v)
    return int(time.time()) - 600  # first run: only the last 10 minutes


def save_since(ts):
    with open(STATE_SINCE, "w") as fh:
        fh.write(str(int(ts)))


def media_from_message(msg):
    """-> list of (url, mime) from imeta tags plus bare /media/ URLs in content."""
    found = []
    for tag in msg.get("tags") or []:
        if not tag or tag[0] != "imeta":
            continue
        url = mime = None
        for item in tag[1:]:
            k, _, v = item.partition(" ")
            if k == "url":
                url = v.strip()
            elif k == "m":
                mime = v.strip()
        if url:
            found.append((url, mime))
    known = {u for u, _ in found}
    for url in MEDIA_RE.findall(msg.get("content") or ""):
        if url not in known:
            found.append((url, None))
    return found


def classify(url, mime):
    ext = os.path.splitext(url.split("?")[0])[1].lower()
    if (mime or "").startswith("audio/") or (mime or "").startswith("video/") or ext in AUDIO_EXT:
        return "audio", ext or ".m4a"
    if (mime or "").startswith("image/") or ext in IMAGE_EXT:
        return "image", ext or ".jpg"
    return "other", ext


def download(url, dest):
    buzz(["media", "get", url, "-o", dest], timeout=120)
    return os.path.exists(dest) and os.path.getsize(dest) > 0


def transcribe_file(path):
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "voice.wav")
        subprocess.run(["ffmpeg", "-y", "-i", path, "-ar", "16000", "-ac", "1", wav],
                       check=True, capture_output=True, timeout=120)
        r = subprocess.run([tc.WHISPER, "-m", tc.MODEL, "-l", LANG, "-f", wav, "--no-timestamps"],
                           capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            log(f"whisper error: {r.stderr[:200]}")
            return None
        return r.stdout.strip()


def write_note(note, stamp, source):
    os.makedirs(tc.CAPTURE_DIR, exist_ok=True)
    path = os.path.join(tc.CAPTURE_DIR, f"{stamp}-buzz.md")
    with open(path, "w") as fh:
        fh.write(note + f"\n\n---\n{t('buzz_capture.source_label')}: Buzz #{CHANNEL_NAME} {source}, {stamp}\n")
    log(f"Note written: {path}")
    return path


def handle(msg):
    """-> (title, path) or raises."""
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    content = (msg.get("content") or "").strip()
    caption = MD_IMAGE_RE.sub("", MEDIA_RE.sub("", content)).strip()
    media = media_from_message(msg)

    audio = [(u, m) for u, m in media if classify(u, m)[0] == "audio"]
    images = [(u, m) for u, m in media if classify(u, m)[0] == "image"]

    if audio:
        url, mime = audio[0]
        ext = classify(url, mime)[1]
        with tempfile.TemporaryDirectory() as tmp:
            raw = os.path.join(tmp, f"voice{ext}")
            if not download(url, raw):
                raise RuntimeError(t("buzz_capture.err_audio_download"))
            log("Audio received, transcribing...")
            text = transcribe_file(raw)
        if not text:
            raise RuntimeError(t("buzz_capture.err_transcript_empty"))
        if caption:
            text = f"{text}\n\n" + t("buzz_capture.caption_note", owner=OWNER, caption=caption)
        note = tc.make_note(text, t("buzz_capture.source_voice_prompt")) or f"# {t('buzz_capture.quick_note_title')}\n\n{text}"
        path = write_note(note, stamp, t("buzz_capture.source_voice"))
        return note.splitlines()[0].lstrip("# ").strip(), path

    if images:
        url, mime = images[0]
        ext = classify(url, mime)[1]
        os.makedirs(tc.CAPTURE_DIR, exist_ok=True)
        dest = os.path.join(tc.CAPTURE_DIR, f"{stamp}-buzz{ext}")
        if not download(url, dest):
            raise RuntimeError(t("buzz_capture.err_image_download"))
        log("Image received, processing...")
        note = tc.make_photo_note(dest, caption) or f"# {t('buzz_capture.image_note_title')}\n\n{caption}".rstrip()
        note += f"\n\n![[{os.path.basename(dest)}]]"
        path = write_note(note, stamp, t("buzz_capture.source_image"))
        return note.splitlines()[0].lstrip("# ").strip(), path

    if caption:
        m = tc.URL_RE.search(caption)
        if m and len(tc.URL_RE.sub("", caption).strip()) < 200 and "/media/" not in m.group(0):
            title = tc.handle_link(caption, m)
            return (title or t("buzz_capture.link_note_title")), os.path.join("Inbox", "Links")
        note = tc.make_note(caption, t("buzz_capture.source_text_prompt")) or f"# {t('buzz_capture.quick_note_title')}\n\n{caption}"
        path = write_note(note, stamp, t("buzz_capture.source_text"))
        return note.splitlines()[0].lstrip("# ").strip(), path

    raise RuntimeError(t("buzz_capture.err_no_content"))


def reply(channel, event_id, text):
    try:
        buzz(["messages", "send", "--channel", channel, "--reply-to", event_id, "--content", "-"],
             stdin=text, timeout=45)
    except Exception as exc:
        log(f"reply could not be sent: {exc}")


def main():
    channel = channel_id(CHANNEL_NAME)
    if not channel or not secret(IDENTITY):
        log("buzz capture: no channel or identity, skipping")
        return
    since = load_since()
    try:
        out = buzz(["messages", "get", "--channel", channel, "--limit", "50", "--since", str(since - 1)])
    except Exception as exc:
        log(f"buzz messages get failed: {exc}")
        return
    try:
        data = json.loads(out or "[]")
        msgs = data if isinstance(data, list) else data.get("messages", [])
    except ValueError:
        log("buzz output is not JSON")
        return

    seen = load_seen()
    authors = allowed_authors()
    newest = since
    msgs.sort(key=lambda m: m.get("created_at", 0))
    for msg in msgs:
        mid = msg.get("id") or msg.get("event_id")
        author = msg.get("pubkey") or msg.get("author")
        created = int(msg.get("created_at") or 0)
        if not mid or mid in seen:
            continue
        seen.add(mid)
        newest = max(newest, created)
        if author not in authors:
            continue
        # Thread replies (our own acks, agent answers) are never captures.
        if any(t and t[0] == "e" for t in msg.get("tags") or []):
            continue
        try:
            title, path = handle(msg)
            rel = os.path.relpath(path, VAULT)
            reply(channel, mid, t("buzz_capture.reply_saved", title=title, rel=rel))
        except Exception as exc:
            log(f"capture error: {exc}")
            reply(channel, mid, t("buzz_capture.reply_failed", exc=exc))
    save_seen(seen)
    save_since(newest)


if __name__ == "__main__":
    main()
