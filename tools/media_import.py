#!/usr/bin/env python3
"""media_import.py: YouTube videos and podcast episodes in, as transcripts.

Video and audio are the highest-volume, lowest-retention sources there are: an
hour-long talk leaves almost nothing a week later. A transcript is text, which
is what the vault wants, so a shared link becomes a dated, timestamped
transcript in Inbox/Media/ and the nightly compile does the rest.

Capture only queues. Telegram and Buzz call enqueue() from handle_link and
answer at once; a long transcription never blocks the two-minute capture
worker. The timer (brainless-media, every 10 minutes on the worker) runs the
queue one job at a time.

  YouTube         captions first (yt-dlp, uploaded before automatic, no video
                  download); no captions, then the audio through Whisper.
  Apple Podcasts  the episode id in the shared link resolves through Apple's
                  public lookup API to the show's own MP3, which Whisper
                  transcribes on this machine. The audio never leaves it.

Then one cleaning pass puts punctuation, paragraphs and speaker turns into
the raw text without cutting anything, and keeps a [mm:ss] marker every few
minutes so a claim can be checked against the recording in ten seconds.

Usage:
  python3 tools/media_import.py add <url> [comment]   queue a link by hand
  python3 tools/media_import.py run [--max 1]          work the queue
  python3 tools/media_import.py list                   show the queue
Needs: yt-dlp (YouTube), ffmpeg and whisper-cli with a ggml model (audio).
"""
import argparse
import hashlib
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
from pathlib import Path

VAULT = Path(os.environ.get("BRAINLESS_VAULT") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(Path(__file__).resolve().parent))
QUEUE = VAULT / ".agents" / "state" / "media_queue"
DONE_LOG = VAULT / ".agents" / "state" / "media_done.jsonl"
OUT_DIR = VAULT / "Inbox" / "Media"
MAX_ATTEMPTS = 3
MAX_SECONDS = 4 * 3600            # longer than this is a backfill, not a capture
MAX_AUDIO_BYTES = 600 * 1024 * 1024
MARK_EVERY = 180                  # seconds between [mm:ss] markers
CLEAN_WORDS = 3500                # words per cleaning call
MODEL = os.path.expanduser(os.environ.get(
    "BRAINLESS_WHISPER_MODEL", "~/.local/share/whisper-models/ggml-large-v3-turbo-q5_0.bin"))

YOUTUBE_RE = re.compile(r"^https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?[^ ]*v=|shorts/|live/)|youtu\.be/)"
                        r"([A-Za-z0-9_-]{11})")
APPLE_RE = re.compile(r"^https?://podcasts\.apple\.com/[^?]*/id(\d+)(?:\?[^ ]*\bi=(\d+))?")


# ─── classify and queue ──────────────────────────────────────────
def classify(url: str) -> str | None:
    if YOUTUBE_RE.match(url):
        return "youtube"
    if APPLE_RE.match(url):
        return "podcast"
    return None


def job_id(url: str) -> str:
    m = YOUTUBE_RE.match(url)
    key = f"yt:{m.group(1)}" if m else url.split("#")[0]
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def enqueue(url: str, comment: str = "", source: str = "manual") -> tuple[str, bool]:
    """Queue a link. (job id, True if new). Sharing the same video twice is one job."""
    kind = classify(url)
    if not kind:
        raise ValueError(f"not a YouTube or Apple Podcasts link: {url}")
    jid = job_id(url)
    QUEUE.mkdir(parents=True, exist_ok=True)
    path = QUEUE / f"{jid}.json"
    if path.exists() or jid in done_ids():
        return jid, False
    path.write_text(json.dumps({"id": jid, "url": url, "kind": kind, "comment": comment,
                                "source": source, "added": datetime.now().isoformat(timespec="seconds"),
                                "attempts": 0}, ensure_ascii=False))
    return jid, True


def done_ids() -> set[str]:
    try:
        return {json.loads(l)["id"] for l in DONE_LOG.read_text().splitlines() if l.strip()}
    except (OSError, ValueError, KeyError):
        return set()


# ─── transcripts ─────────────────────────────────────────────────
def _ts(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


def _secs(stamp: str) -> float:
    parts = stamp.replace(",", ".").split(":")
    parts = [0.0] * (3 - len(parts)) + [float(p) for p in parts]
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def parse_vtt(text: str) -> list[tuple[float, str]]:
    """WebVTT -> [(start seconds, text)]. Automatic captions repeat each line as
    it scrolls; a line already seen in the last few cues is dropped."""
    cues, recent = [], []
    for block in re.split(r"\n\s*\n", text):
        m = re.search(r"(\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3})\s+-->", block)
        if not m:
            continue
        lines = block[m.end():].split("\n")[1:]
        for line in lines:
            line = re.sub(r"<[^>]+>", "", line).strip()
            line = re.sub(r"&nbsp;|&amp;", lambda x: " " if "nbsp" in x.group(0) else "&", line)
            if not line or line in recent:
                continue
            cues.append((_secs(m.group(1)), line))
            recent = (recent + [line])[-4:]
    return cues


def parse_whisper(stdout: str) -> list[tuple[float, str]]:
    """whisper-cli output lines '[00:00:01.000 --> 00:00:04.000]  text'."""
    out = []
    for m in re.finditer(r"^\[(\d{2}:\d{2}:\d{2}[.,]\d{3}) --> [^\]]+\]\s*(.*)$", stdout, re.M):
        if m.group(2).strip():
            out.append((_secs(m.group(1)), m.group(2).strip()))
    return out


def paragraphs(cues, chapters=()) -> str:
    """Cues -> text with a [mm:ss] marker every few minutes and a heading at each chapter."""
    chapters = sorted(chapters, key=lambda c: c[0])
    out, buf, next_mark, ci = [], [], 0.0, 0
    for start, text in cues:
        while ci < len(chapters) and start >= chapters[ci][0]:
            if buf:
                out.append(" ".join(buf))
                buf = []
            out.append(f"## {chapters[ci][1]}")
            ci += 1
            next_mark = 0.0
        if start >= next_mark:
            if buf:
                out.append(" ".join(buf))
                buf = []
            buf.append(f"[{_ts(start)}]")
            next_mark = start + MARK_EVERY
        buf.append(text)
    if buf:
        out.append(" ".join(buf))
    return "\n\n".join(out)


# ─── sources ─────────────────────────────────────────────────────
def _run(cmd, timeout):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def youtube(url: str, tmp: Path) -> dict:
    ytdlp = shutil.which("yt-dlp")
    if not ytdlp:
        raise RuntimeError("yt-dlp is not installed")
    meta = json.loads(_run([ytdlp, "-J", "--skip-download", "--no-playlist", url], 120).stdout or "{}")
    if not meta:
        raise RuntimeError("yt-dlp returned no metadata (private, removed or region-locked?)")
    if (meta.get("duration") or 0) > MAX_SECONDS:
        raise RuntimeError(f"too long ({_ts(meta['duration'])}); import it by hand if it is worth it")
    info = {"kind": "youtube", "title": meta.get("title") or "untitled",
            "author": meta.get("channel") or meta.get("uploader") or "",
            "published": _ymd(meta.get("upload_date")), "duration": meta.get("duration") or 0,
            "url": meta.get("webpage_url") or url,
            "chapters": [(c.get("start_time", 0), c.get("title", "")) for c in meta.get("chapters") or []]}
    langs = "tr,en,tr-orig,en-orig,en-US,en-GB"
    _run([ytdlp, "--skip-download", "--no-playlist", "--write-subs", "--write-auto-subs", "--sub-langs", langs,
          "--sub-format", "vtt", "-o", str(tmp / "sub.%(ext)s"), url], 180)
    subs = sorted(tmp.glob("sub*.vtt"), key=lambda p: (".orig" in p.name, "auto" in p.name))
    if subs:
        info["cues"], info["method"] = parse_vtt(subs[0].read_text(errors="replace")), "captions"
        if info["cues"]:
            return info
    r = _run([ytdlp, "-x", "--audio-format", "mp3", "--no-playlist", "-o", str(tmp / "audio.%(ext)s"), url], 1800)
    audio = next(tmp.glob("audio.*"), None)
    if r.returncode != 0 or not audio:
        raise RuntimeError(f"no captions and the audio download failed: {r.stderr[-200:]}")
    info["cues"], info["method"] = whisper(audio, tmp), "whisper"
    return info


def _ymd(s) -> str:
    s = str(s or "")
    if re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s[:10] if re.match(r"\d{4}-\d{2}-\d{2}", s) else ""


def apple_lookup(show_id: str, episode_id: str, fetch=None) -> dict:
    """The episode's own MP3 and metadata, from Apple's public lookup API."""
    fetch = fetch or _get_json
    data = fetch(f"https://itunes.apple.com/lookup?id={show_id}&entity=podcastEpisode&limit=300")
    for r in data.get("results", []):
        if str(r.get("trackId")) == str(episode_id) and r.get("episodeUrl"):
            return {"kind": "podcast", "title": r.get("trackName") or "untitled",
                    "author": r.get("collectionName") or "", "published": _ymd(r.get("releaseDate")),
                    "duration": int((r.get("trackTimeMillis") or 0) / 1000), "audio": r["episodeUrl"],
                    "description": (r.get("description") or "")[:600]}
    raise RuntimeError("episode not found in Apple's lookup (older than the newest 300 episodes?)")


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "brainless-media/1"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read(5_000_000).decode("utf-8", "replace"))


def _public(url: str) -> bool:
    host = urllib.parse.urlparse(url).hostname or ""
    if urllib.parse.urlparse(url).scheme not in ("http", "https") or not host:
        return False
    try:
        sys.path.insert(0, str(VAULT / ".agents" / "scripts"))
        from telegram_capture import _is_public_host
        return _is_public_host(host)
    except Exception:
        return True


def download(url: str, dest: Path):
    if not _public(url):
        raise RuntimeError("audio URL points at a private address; refused")
    req = urllib.request.Request(url, headers={"User-Agent": "brainless-media/1"})
    size = 0
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as fh:
        while chunk := r.read(1 << 20):
            size += len(chunk)
            if size > MAX_AUDIO_BYTES:
                raise RuntimeError("audio larger than the cap; refused")
            fh.write(chunk)


def podcast(url: str, tmp: Path) -> dict:
    m = APPLE_RE.match(url)
    if not m or not m.group(2):
        raise RuntimeError("that is a show link; share one episode (the link has ?i=...)")
    info = apple_lookup(m.group(1), m.group(2))
    if info["duration"] > MAX_SECONDS:
        raise RuntimeError(f"too long ({_ts(info['duration'])}); import it by hand if it is worth it")
    audio = tmp / "episode.audio"
    download(info["audio"], audio)
    info.update({"url": url, "chapters": [], "cues": whisper(audio, tmp), "method": "whisper"})
    return info


def whisper_bin() -> str:
    return next((p for p in [shutil.which("whisper-cli"), "/opt/homebrew/bin/whisper-cli",
                             os.path.expanduser("~/build/whisper.cpp/build/bin/whisper-cli")]
                 if p and os.path.exists(p)), "")


def whisper(audio: Path, tmp: Path) -> list[tuple[float, str]]:
    exe = whisper_bin()
    if not exe or not os.path.exists(MODEL):
        raise RuntimeError("whisper-cli or its model is missing")
    wav = tmp / "audio.wav"
    subprocess.run(["ffmpeg", "-y", "-i", str(audio), "-ar", "16000", "-ac", "1", str(wav)],
                   check=True, capture_output=True, timeout=1800)
    r = _run([exe, "-m", MODEL, "-l", "auto", "-t", str(max(1, (os.cpu_count() or 4) - 1)), "-f", str(wav)],
             4 * 3600)
    cues = parse_whisper(r.stdout)
    if r.returncode != 0 or not cues:
        raise RuntimeError(f"whisper produced nothing: {r.stderr[-200:]}")
    return cues


# ─── clean and write ─────────────────────────────────────────────
def clean(text: str, info: dict) -> str:
    """Punctuation, paragraphs, speaker turns, names fixed; nothing cut. A chunk
    the model fails on stays raw: a rough transcript beats no transcript."""
    try:
        from llm import run_prompt
    except Exception:
        return text
    words, chunks, cur = text.split(" "), [], []
    for w in words:
        cur.append(w)
        if len(cur) >= CLEAN_WORDS and w.endswith((".", "?", "!", "]")):
            chunks.append(" ".join(cur))
            cur = []
    if cur:
        chunks.append(" ".join(cur))
    out = []
    for chunk in chunks:
        prompt = f"""This is part of a raw {'podcast' if info['kind'] == 'podcast' else 'video'} transcript: "{info['title']}" ({info['author']}).
Add punctuation and paragraph breaks. Where the speaker clearly changes, start
the paragraph with **Speaker 1:**, **Speaker 2:** (or the name if the text says it).
Fix obvious mis-transcriptions of names and technical terms. Keep every [mm:ss]
marker and every ## heading exactly where it is. Do NOT summarise, shorten or
skip anything. Do NOT use em dashes or en dashes. Keep the original language.
Return only the cleaned text.

{chunk}"""
        got = run_prompt(prompt, timeout=600, lane="media-clean")
        ok = got and len(got.split()) >= 0.8 * len(chunk.split())
        out.append(got.strip() if ok else chunk)
    return "\n\n".join(out).replace(chr(0x2014), ",").replace(chr(0x2013), "-")


def slug(s: str) -> str:
    s = re.sub(r"[\\/:*?\"<>|#^\[\]]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()[:90] or "untitled"


def render(info: dict, body: str, job: dict) -> str:
    esc = lambda s: str(s).replace('"', "'").replace(chr(0x2014), ",").replace(chr(0x2013), "-")
    fm = [f'title: "{esc(info["title"])}"', f"kind: {info['kind']}", f"url: {info['url']}",
          f'author: "{esc(info["author"])}"', f"published: {info['published'] or 'unknown'}",
          f"duration: {_ts(info['duration'])}", f"transcript: {info['method']}",
          f"captured: {datetime.now().strftime('%Y-%m-%d')}"]
    head = f"# {esc(info['title'])}\n\n"
    if job.get("comment"):
        head += f"> {esc(job['comment'])}\n\n"
    if info.get("description"):
        head += f"{esc(info['description'])}\n\n"
    return "---\n" + "\n".join(fm) + "\n---\n" + head + "## Transcript\n\n" + body + "\n"


def process(job: dict) -> Path:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        info = youtube(job["url"], tmp) if job["kind"] == "youtube" else podcast(job["url"], tmp)
    body = clean(paragraphs(info["cues"], info.get("chapters", ())), info)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    date = info["published"] or datetime.now().strftime("%Y-%m-%d")
    path = OUT_DIR / f"{date} {slug(info['title'])}.md"
    path.write_text(render(info, body, job))
    return path


def notify(text: str, key: str):
    try:
        from buzz_delivery import send
        send("inbox", text, key=key)
    except Exception as e:
        print(f"buzz notify failed: {e}", file=sys.stderr)


def run(max_jobs: int = 1) -> tuple[int, int]:
    done = failed = 0
    jobs = sorted(QUEUE.glob("*.json")) if QUEUE.exists() else []
    for jp in jobs[:max_jobs]:
        job = json.loads(jp.read_text())
        job["attempts"] = job.get("attempts", 0) + 1
        t0 = time.monotonic()
        try:
            path = process(job)
        except Exception as e:
            reason = str(e)[:300]
            print(f"[FAIL] {job['url']}: {reason}", file=sys.stderr)
            if job["attempts"] >= MAX_ATTEMPTS or "not found" in reason or "show link" in reason \
                    or "too long" in reason:
                jp.unlink()
                _log_done(job, "failed", reason)
                notify(f"Transkript alınamadı: {job['url']}\n{reason}", f"media-fail:{job['id']}")
            else:
                jp.write_text(json.dumps(job, ensure_ascii=False))
            failed += 1
            continue
        jp.unlink()
        rel = path.relative_to(VAULT)
        _log_done(job, "done", str(rel))
        words = len(path.read_text().split())
        print(f"[ok] {rel} ({words} words, {time.monotonic() - t0:.0f}s)")
        notify(f"Transkript hazır: {path.stem}\n{words} kelime, {rel}", f"media-done:{job['id']}")
        done += 1
    return done, failed


def _log_done(job, status, detail):
    DONE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(DONE_LOG, "a") as fh:
        fh.write(json.dumps({"id": job["id"], "url": job["url"], "status": status, "detail": detail,
                             "at": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("url")
    a.add_argument("comment", nargs="*")
    r = sub.add_parser("run")
    r.add_argument("--max", type=int, default=1)
    sub.add_parser("list")
    args = ap.parse_args(argv)
    if args.cmd == "add":
        jid, new = enqueue(args.url, " ".join(args.comment))
        print(f"{'queued' if new else 'already queued or done'}: {jid}")
    elif args.cmd == "list":
        for jp in sorted(QUEUE.glob("*.json")) if QUEUE.exists() else []:
            j = json.loads(jp.read_text())
            print(f"{j['id']}  {j['kind']:<8} attempts={j['attempts']}  {j['url']}")
    else:
        done, failed = run(args.max)
        left = len(list(QUEUE.glob("*.json"))) if QUEUE.exists() else 0
        print(f"RUNLOG transcribed={done} failed={failed} queued={left}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
