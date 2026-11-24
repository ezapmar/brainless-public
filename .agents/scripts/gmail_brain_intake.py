#!/usr/bin/env python3
"""gmail_brain_intake.py: controlled, idempotent Gmail intake by label.

Polls Gmail over read-only IMAP for messages carrying a dedicated label
(default: "Brain", override with --label or BRAINLESS_GMAIL_LABEL) and saves
each new message as a traceable Markdown note under Inbox/Mail/.

Design guarantees:
- Read-only mailbox access (SELECT ... readonly=True): Gmail is never altered.
- Idempotent state (.agents/state/gmail_intake_state.json): a Message-ID is
  recorded ONLY AFTER its Markdown note is atomically written to disk, so a
  second run over the same label produces zero duplicates.
- Local-only privacy by default: every saved mail note carries
  `privacy: personal` so tools/compile_resources.py compiles it on the local
  Gemma 4 12B lane rather than sending raw mail to a cloud provider.
- Graceful opt-in: if credentials are absent or the Brain label does not exist
  yet in Gmail, exits 0 cleanly so hourly runs never turn health_check red.
- Review queue: attachment-heavy or body-less messages are flagged in
  _Agent-Context/GMAIL-REVIEW.md for manual follow-up.

Credentials (outside the repo, ~/.config/brainless/, 0600; shared with spiky_capture):
  gmail_address      : IMAP account
  gmail_app_password : Google app password (requires 2FA)

Usage:
  python3 .agents/scripts/gmail_brain_intake.py                 # up to 5 new mails
  python3 .agents/scripts/gmail_brain_intake.py --limit 10      # up to 10 new mails
  python3 .agents/scripts/gmail_brain_intake.py --dry-run       # preview, write nothing
  python3 .agents/scripts/gmail_brain_intake.py --label Brain   # explicit label
"""
import argparse
import email
import hashlib
import imaplib
import json
import os
import re
import ssl
import sys
from datetime import datetime
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

VAULT = Path(os.environ.get("BRAINLESS_VAULT") or Path(__file__).resolve().parents[2])
TOOLS = VAULT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from fsutil import atomic_write
from logline import log

CONF_DIR = Path(os.path.expanduser("~/.config/brainless"))
STATE_FILE = VAULT / ".agents" / "state" / "gmail_intake_state.json"
OUT_DIR = VAULT / "Inbox" / "Mail"
REVIEW_FILE = VAULT / "_Agent-Context" / "GMAIL-REVIEW.md"
DEFAULT_LABEL = os.environ.get("BRAINLESS_GMAIL_LABEL", "Brain")
DEFAULT_LIMIT = 5


def _read_secret(path: Path) -> str | None:
    try:
        val = path.read_text(encoding="utf-8").strip()
        return val or None
    except OSError:
        return None


class _HTMLToText(HTMLParser):
    BLOCK = {"p", "div", "tr", "table", "ul", "ol", "br", "h1", "h2", "h3", "h4", "blockquote"}
    SKIP = {"style", "script", "head", "title"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(raw_html: str) -> str:
    parser = _HTMLToText()
    parser.feed(raw_html)
    text = "".join(parser.parts)
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def decode_mime_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except Exception:
        return value.strip()


def slugify(text: str, max_len: int = 56) -> str:
    tr_map = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    cleaned = text.translate(tr_map).lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", cleaned).strip("-")
    return (cleaned[:max_len].rstrip("-")) or "mail"


def normalise_message_id(msg_id: str | None, raw_bytes: bytes) -> str:
    cleaned = (msg_id or "").strip()
    if cleaned:
        return cleaned
    digest = hashlib.sha256(raw_bytes).hexdigest()[:16]
    return f"<sha256-{digest}@brainless.local>"


def parse_message(raw_bytes: bytes) -> dict:
    """Parse an RFC822 message into a structured record."""
    msg = email.message_from_bytes(raw_bytes)
    subject = decode_mime_header(msg.get("Subject")) or "(no subject)"
    sender_name, sender_addr = parseaddr(decode_mime_header(msg.get("From")))
    sender = f"{sender_name} <{sender_addr}>" if sender_name and sender_addr else (sender_addr or sender_name or "unknown")
    message_id = normalise_message_id(msg.get("Message-ID"), raw_bytes)

    date_hdr = msg.get("Date")
    try:
        observed_dt = parsedate_to_datetime(date_hdr) if date_hdr else datetime.now().astimezone()
        if observed_dt.tzinfo is None:
            observed_dt = observed_dt.astimezone()
    except Exception:
        observed_dt = datetime.now().astimezone()

    plain_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            disp = (part.get_content_disposition() or "").lower()
            filename = decode_mime_header(part.get_filename())
            ctype = (part.get_content_type() or "").lower()
            if disp == "attachment" or filename:
                attachments.append(filename or ctype)
                continue
            if ctype not in ("text/plain", "text/html"):
                continue
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="replace")
            if ctype == "text/plain":
                plain_parts.append(decoded)
            else:
                html_parts.append(decoded)
    else:
        payload = msg.get_payload(decode=True)
        if payload is not None:
            charset = msg.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="replace")
            if (msg.get_content_type() or "").lower() == "text/html":
                html_parts.append(decoded)
            else:
                plain_parts.append(decoded)

    body = "\n\n".join(p.strip() for p in plain_parts if p.strip()).strip()
    if not body and html_parts:
        body = html_to_text("\n\n".join(html_parts))
    body = re.sub(r"\r\n?", "\n", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()

    review_reasons: list[str] = []
    if attachments:
        review_reasons.append(f"attachments not extracted ({', '.join(attachments[:5])})")
    if len(body) < 25:
        review_reasons.append("empty or very short body")

    return {
        "message_id": message_id,
        "subject": subject,
        "sender": sender,
        "observed_dt": observed_dt,
        "body": body,
        "attachments": attachments,
        "review_reasons": review_reasons,
    }


def load_state(state_path: Path = STATE_FILE) -> dict:
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("processed"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"processed": {}}


def save_state(state: dict, state_path: Path = STATE_FILE) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(state_path, json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _yaml_quote(value: str) -> str:
    clean = value.replace("\u2014", "-").replace("\u2013", "-").replace("\n", " ").strip()
    escaped = clean.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_note(record: dict, label: str, checked_at: datetime) -> str:
    day = record["observed_dt"].strftime("%Y-%m-%d")
    observed = record["observed_dt"].strftime("%Y-%m-%d %H:%M")
    checked = checked_at.strftime("%Y-%m-%d %H:%M")
    att_list = ", ".join(_yaml_quote(a) for a in record["attachments"])
    needs_review = "true" if record["review_reasons"] else "false"
    subject_clean = record["subject"].replace("\u2014", "-").replace("\u2013", "-")

    lines = [
        "---",
        f"date: {day}",
        "type: mail-intake",
        "source: gmail",
        f"gmail_label: {_yaml_quote(label)}",
        f"message_id: {_yaml_quote(record['message_id'])}",
        f"sender: {_yaml_quote(record['sender'])}",
        f"observed: {_yaml_quote(observed)}",
        f"status_checked: {_yaml_quote(checked)}",
        f"attachments: [{att_list}]",
        f"needs_review: {needs_review}",
        "tags: [inbox/mail, status/seed]",
        "privacy: personal",
        "---",
        "",
        f"# {subject_clean}",
        "",
        "## Traceable Record",
        f"- **From:** {record['sender']}",
        f"- **Observed:** {observed}",
        f"- **Status checked:** {checked}",
        f"- **Message-ID:** `{record['message_id']}`",
        f"- **Gmail label:** `{label}`",
    ]
    if record["attachments"]:
        lines.append(f"- **Attachments (kept in Gmail):** {', '.join(record['attachments'])}")
    if record["review_reasons"]:
        lines.append(f"- **Open question:** {'; '.join(record['review_reasons'])}")
    lines += [
        "",
        "## Body (untrusted source data)",
        "",
        record["body"] or "_No plain-text or HTML body extracted._",
        "",
    ]
    return "\n".join(lines)


def note_filename(record: dict) -> str:
    day = record["observed_dt"].strftime("%Y-%m-%d")
    slug = slugify(record["subject"])
    short_hash = hashlib.sha256(record["message_id"].encode("utf-8")).hexdigest()[:6]
    return f"{day}-{slug}-{short_hash}.md"


def append_review_items(items: list[dict], review_path: Path = REVIEW_FILE,
                        checked_at: datetime | None = None) -> None:
    if not items:
        return
    checked = (checked_at or datetime.now()).strftime("%Y-%m-%d %H:%M")
    if review_path.exists():
        existing = review_path.read_text(encoding="utf-8").rstrip("\n")
    else:
        existing = (
            "# Gmail Brain Intake Review Queue\n\n"
            "> Flagged automatically by `.agents/scripts/gmail_brain_intake.py` when a labelled\n"
            "> message has attachments not extracted by IMAP or an empty/ambiguous body."
        )
    new_lines = [f"\n\n## {checked}"]
    for it in items:
        reasons = "; ".join(it["review_reasons"])
        subj = it["subject"].replace("\u2014", "-").replace("\u2013", "-")
        new_lines.append(
            f"- [ ] [[{it['note_rel']}]] ({subj}) from `{it['sender']}`: {reasons}"
        )
    review_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(review_path, existing + "\n".join(new_lines) + "\n")


def ingest_messages(raw_messages: list[bytes], label: str = DEFAULT_LABEL,
                    limit: int = DEFAULT_LIMIT, dry_run: bool = False,
                    out_dir: Path = OUT_DIR, state_path: Path = STATE_FILE,
                    review_path: Path = REVIEW_FILE, vault_root: Path = VAULT,
                    now: datetime | None = None) -> dict:
    """Process raw RFC822 messages idempotently. Returns summary counts."""
    checked_at = now or datetime.now()
    state = load_state(state_path)
    processed = state["processed"]

    written = 0
    skipped = 0
    flagged: list[dict] = []

    for raw_bytes in raw_messages:
        if limit > 0 and written >= limit:
            break
        rec = parse_message(raw_bytes)
        mid = rec["message_id"]
        if mid in processed:
            skipped += 1
            continue

        fname = note_filename(rec)
        dest = out_dir / fname
        try:
            note_rel = dest.relative_to(vault_root).with_suffix("").as_posix()
        except ValueError:
            note_rel = fname.removesuffix(".md")

        if dry_run:
            log(f"[dry-run] would write {dest} (mid={mid})")
            written += 1
            if rec["review_reasons"]:
                flagged.append({**rec, "note_rel": note_rel})
            continue

        out_dir.mkdir(parents=True, exist_ok=True)
        content = render_note(rec, label, checked_at)
        atomic_write(dest, content)

        # Record Message-ID ONLY AFTER the note is durably written.
        processed[mid] = {
            "saved_at": checked_at.isoformat(timespec="seconds"),
            "file": str(dest.relative_to(vault_root)) if dest.is_relative_to(vault_root) else str(dest),
            "subject": rec["subject"][:120],
        }
        save_state(state, state_path)
        written += 1
        log(f"Mail note written: {dest}")

        if rec["review_reasons"]:
            flagged.append({**rec, "note_rel": note_rel})

    if flagged and not dry_run:
        append_review_items(flagged, review_path=review_path, checked_at=checked_at)

    return {"written": written, "skipped": skipped, "flagged": len(flagged)}


def fetch_label_messages(addr: str, pw: str, label: str, limit: int) -> tuple[bool, list[bytes]]:
    """Connect read-only to Gmail IMAP and fetch recent messages in `label`.
    Returns (label_exists, raw_messages_newest_first)."""
    M = imaplib.IMAP4_SSL("imap.gmail.com", ssl_context=ssl.create_default_context())
    try:
        M.login(addr, pw)
        typ, _ = M.select(f'"{label}"', readonly=True)
        if typ != "OK":
            return False, []
        typ, data = M.uid("search", None, "ALL")
        if typ != "OK" or not data or not data[0]:
            return True, []
        uids = sorted((int(u) for u in data[0].split()), reverse=True)
        # Fetch a modest window (newest first) so already-processed mails can be skipped
        # while still reaching `limit` fresh messages without downloading the whole archive.
        window = uids[:max(limit * 5, 25)] if limit > 0 else uids
        messages: list[bytes] = []
        for uid in window:
            typ, msgdata = M.uid("fetch", str(uid), "(RFC822)")
            if typ == "OK" and msgdata and msgdata[0] and isinstance(msgdata[0], tuple):
                messages.append(msgdata[0][1])
        return True, messages
    finally:
        try:
            M.logout()
        except Exception:
            pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Idempotent Gmail label intake for brainless.")
    ap.add_argument("--label", default=DEFAULT_LABEL, help=f"Gmail label to poll (default: {DEFAULT_LABEL})")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                    help=f"Maximum new messages to save per run (0 = unlimited, default: {DEFAULT_LIMIT})")
    ap.add_argument("--dry-run", action="store_true", help="Preview new mails without writing notes or state")
    args = ap.parse_args(argv)

    addr = _read_secret(CONF_DIR / "gmail_address")
    pw = _read_secret(CONF_DIR / "gmail_app_password")
    if not (addr and pw):
        return 0

    label_exists, raw_messages = fetch_label_messages(addr, pw, args.label, args.limit)
    if not label_exists:
        log(f"Gmail label '{args.label}' not found yet; create it in Gmail to activate intake.")
        print("RUNLOG gmail_intake=0 skipped=0 flagged=0 label_missing=1")
        return 0

    counts = ingest_messages(raw_messages, label=args.label, limit=args.limit, dry_run=args.dry_run)
    print(
        f"RUNLOG gmail_intake={counts['written']} skipped={counts['skipped']} "
        f"flagged={counts['flagged']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
