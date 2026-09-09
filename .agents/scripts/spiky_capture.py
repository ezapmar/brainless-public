#!/usr/bin/env python3
"""Spiky toplanti raporu ingest'i (worker'de calisir).

Gmail'i IMAP ile yoklar, no-reply@report.spiky.ai raporlarini Inbox/Spiky/
altina markdown not olarak dusurur. Rapor mailin govdesinde tam olarak var
(ozet, aksiyonlar, skorlar, detayli dokum), o yuzden LLM'siz ve $0.

State: .agents/state/spiky_uid (son islenen IMAP UID). Ilk calismada eski
raporlari BOCA ETMEZ: mevcut en yuksek UID'yi baseline alir, sonrakiler akar.
Geri doldurma: --backfill N (son N gunun raporlarini da isler).

Kimlik (repo disi, ~/.config/brainless/, 0600):
  gmail_address      : IMAP hesabi
  gmail_app_password : Google uygulama sifresi (2FA gerektirir)
"""
import argparse
import email
import imaplib
import os
import re
import ssl
from datetime import datetime, timedelta
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser

VAULT = os.environ.get("BRAINLESS_VAULT") or os.path.expanduser("~/projects/brainless")
CONF_DIR = os.path.expanduser("~/.config/brainless")
STATE_FILE = os.path.join(VAULT, ".agents", "state", "spiky_uid")
OUT_DIR = os.path.join(VAULT, "Inbox", "Spiky")
SENDER = "no-reply@report.spiky.ai"
FOOTER_MARK = "Please do not reply to this email"


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def read_file(path):
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return None


class _TextExtractor(HTMLParser):
    """Spiky maili cogu zaman salt text/html; blok yapisini koruyarak metne indir."""
    BLOCK = {"p", "div", "tr", "table", "ul", "ol", "br", "h1", "h2", "h3", "h4"}
    SKIP = {"style", "script", "head", "title"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n* ")
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


def html_to_text(html):
    p = _TextExtractor()
    p.feed(html)
    return "".join(p.parts)


def _decode(part):
    return part.get_payload(decode=True).decode(
        part.get_content_charset() or "utf-8", "replace")


def plain_body(msg):
    plain, html = None, None
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        ctype = part.get_content_type()
        if ctype == "text/plain" and plain is None:
            plain = _decode(part)
        elif ctype == "text/html" and html is None:
            html = _decode(part)
    if plain:
        return plain
    if html:
        return html_to_text(html)
    return ""


def clean_body(text):
    cut = text.find(FOOTER_MARK)
    if cut != -1:
        text = text[:cut]
    text = re.sub("[\u200b-\u200f\u2060\ufeff\u00ad\u034f]", "", text)
    text = text.replace("\u202f", " ").replace("\u00a0", " ")
    text = text.replace("\u2014", "-").replace("\u2013", "-")  # house style: dash yasagi
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def safe_name(title):
    title = title.replace("—", "-").replace("–", "-")
    return re.sub(r"[^\w\sÇçĞğİıÖöŞşÜü&.-]", "", title).strip()[:80] or "Rapor"


def process(uid, raw):
    msg = email.message_from_bytes(raw)
    # IMAP FROM aramasi substring eslesir; "no-reply@report.spiky.ai" <x@y> gibi
    # sahte gonderen gecebilir. Gercek From adresini birebir dogrula, yoksa
    # saldirgan metni LLM prompt'larina sokan giris noktasi olur.
    sender = parseaddr(msg.get("From", ""))[1].strip().lower()
    if sender != SENDER.lower():
        log(f"uid {uid}: sahte gonderen '{sender}', atlandi")
        return
    subject = str(make_header(decode_header(msg.get("Subject", ""))))
    title = re.sub(r"^Spiky Report:\s*", "", subject).strip() or "Spiky Raporu"
    try:
        when = parsedate_to_datetime(msg["Date"]).astimezone()
    except Exception:
        when = datetime.now().astimezone()
    body = clean_body(plain_body(msg))
    if not body:
        log(f"uid {uid}: bos govde, atlandi")
        return
    body = body[:200_000]  # kotu niyetli/bozuk mail vault'u sisirmesin
    os.makedirs(OUT_DIR, exist_ok=True)
    fname = f"{when.strftime('%Y-%m-%d')} {safe_name(title)}.md"
    path = os.path.join(OUT_DIR, fname)
    if os.path.exists(path):  # ayni gun ayni baslik: saat ekle, ustune yazma
        alt = os.path.join(OUT_DIR, f"{when.strftime('%Y-%m-%d %H%M')} {safe_name(title)}.md")
        if os.path.exists(alt):
            return  # ayni rapor zaten islenmis (backfill tekrar kosuldu)
        path = alt
    with open(path, "w") as fh:
        fh.write(f"# {title}\n\nTarih: {when.strftime('%Y-%m-%d %H:%M')}\n"
                 f"Kaynak: Spiky raporu (email ingest, worker)\n\n---\n\n{body}\n")
    log(f"Rapor yazildi: {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", type=int, default=0, metavar="GUN",
                    help="son N gunun raporlarini da isle")
    args = ap.parse_args()

    addr = read_file(os.path.join(CONF_DIR, "gmail_address"))
    pw = read_file(os.path.join(CONF_DIR, "gmail_app_password"))
    if not (addr and pw):
        return  # kimlik yoksa sessizce cik (telegram_capture ile ayni davranis)

    last = read_file(STATE_FILE)
    # imaplib PEP 476 disinda kaldi: ssl_context verilmezse sertifika DOGRULANMAZ
    # (CERT_NONE). Ofis aginda aktif MITM app password'u calabilir. Dogrulat.
    M = imaplib.IMAP4_SSL("imap.gmail.com", ssl_context=ssl.create_default_context())
    M.login(addr, pw)
    folder = "INBOX"
    if args.backfill:
        # Arsivlenmis raporlar INBOX'ta gorunmez; All Mail'i bul (dil bagimsiz, \All flag).
        typ, boxes = M.list()
        for line in boxes or []:
            s = line.decode(errors="replace")
            if "\\All" in s:
                m = re.search(r'"([^"]+)"\s*$', s)
                if m:
                    folder = m.group(1)
                break
    M.select(f'"{folder}"', readonly=True)

    criteria = ["FROM", SENDER]
    if args.backfill:
        since = (datetime.now() - timedelta(days=args.backfill)).strftime("%d-%b-%Y")
        criteria += ["SINCE", since]
    typ, data = M.uid("search", None, *criteria)
    uids = sorted(int(u) for u in (data[0].split() if data and data[0] else []))
    if not uids:
        M.logout()
        return

    if last is None and not args.backfill:
        # Ilk calisma: baseline kur, eskiyi isleme.
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as fh:
            fh.write(str(uids[-1]))
        log(f"Baseline kuruldu (uid {uids[-1]}); yeni raporlar akmaya baslar.")
        M.logout()
        return

    floor = 0 if args.backfill else int(last or 0)
    new = [u for u in uids if u > floor] if not args.backfill else uids
    for uid in new:
        typ, msgdata = M.uid("fetch", str(uid), "(RFC822)")
        if typ == "OK" and msgdata and msgdata[0]:
            try:
                process(uid, msgdata[0][1])
            except Exception as e:
                log(f"uid {uid} hata: {e}")
    if not args.backfill:
        # Backfill All Mail UID'leriyle calisir; INBOX baseline'ina dokunma.
        top = max(uids[-1], int(last or 0))
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as fh:
            fh.write(str(top))
    M.logout()


if __name__ == "__main__":
    main()
