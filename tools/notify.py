"""Durable Telegram outbox: every notice, question and approval goes here.

Replaced the Buzz outbox on 2026-11-15 (single Mac, no relay). The workflow
code (today queue, thinking loop, dreaming, concept review) was written against
Buzz threads; this module keeps that contract so the workflows did not change:

  send(channel, body)       queue and deliver one message, idempotent by key
  Outbox.enqueue/deliver    the same durable sqlite table, retried with backoff
  event_id(msg)             "tg<message_id>" for Telegram messages
  messages in `incoming`    owner replies, stored by telegram_capture.py in the
                            Buzz message shape ({id, content, pubkey, tags})

Everything goes to the one whitelisted chat. The old Buzz channel becomes a
first-line label ("#tasks"), and a reply is routed back by the channel of the
message it answers (Outbox.channel_of), not by where it was typed.
The bot token never enters logs or error rows.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

from vault_lock import lock_exclusive

VAULT = Path(os.environ.get('BRAINLESS_VAULT') or Path(__file__).resolve().parents[1])
CONF_DIR = Path.home() / '.config/brainless'
# Channel -> identity. The identity no longer selects a key; it is kept because
# outbox keys and workflow state were built from it.
ROUTES = {'ops': 'watchdog', 'tasks': 'tasks', 'radar': 'radar', 'content': 'content',
          'daily': 'briefing', 'inbox': 'inbox', 'thinking': 'thinking', 'crm': 'crm',
          'writing': 'writing', 'dreaming': 'dreaming', 'ask': 'ask', 'projects': 'projects'}
EVENT_RE = re.compile(r'tg\d+')
MAX_TEXT = 4000  # Telegram caps a message at 4096 characters
QUIET_CHANNELS = frozenset({'crm', 'radar', 'content'})  # delivered without a sound


def event_id(value):
    if isinstance(value, dict):
        for key in ('id', 'event_id', 'eventId'):
            v = value.get(key)
            if isinstance(v, str) and EVENT_RE.fullmatch(v):
                return v
        return None
    return value if isinstance(value, str) and EVENT_RE.fullmatch(value) else None


def tg_event(message_id):
    return f'tg{int(message_id)}'


def _read(path):
    try:
        return Path(path).read_text(encoding='utf-8').strip()
    except OSError:
        return ''


class Telegram:
    """The one bot and the one chat. Same surface the outbox used on Buzz."""

    def __init__(self, conf=CONF_DIR):
        self.conf = Path(conf)

    def token(self):
        token = os.environ.get('BRAINLESS_TELEGRAM_TOKEN') or _read(self.conf / 'telegram_token')
        if not token:
            raise ValueError('Telegram token is not configured')
        return token

    def owner(self):
        chat = os.environ.get('BRAINLESS_TELEGRAM_CHAT') or _read(self.conf / 'telegram_chat_id')
        if not re.fullmatch(r'-?\d+', chat):
            raise ValueError('Telegram chat is not configured')
        return chat

    def channel(self, name, identity=None):
        return name

    def api(self, method, params):
        url = f'https://api.telegram.org/bot{self.token()}/{method}'
        data = urllib.parse.urlencode(params).encode()
        try:
            with urllib.request.urlopen(url, data=data, timeout=30) as resp:
                body = json.load(resp)
        except Exception as exc:
            # The URL carries the token; never let it reach a log line.
            raise RuntimeError(f'Telegram {method} failed ({type(exc).__name__})') from None
        if not body.get('ok'):
            raise RuntimeError(f'Telegram {method} refused')
        return body['result']

    def post(self, channel, body, parent=None):
        text = f'#{channel}\n{body.strip()}'
        if len(text) > MAX_TEXT:
            text = text[:MAX_TEXT - 20].rstrip() + '\n[...kısaltıldı]'
        params = {'chat_id': self.owner(), 'text': text, 'disable_web_page_preview': 'true'}
        if channel in QUIET_CHANNELS:
            params['disable_notification'] = 'true'
        if parent:
            params['reply_parameters'] = json.dumps({'message_id': int(parent[2:]),
                                                     'allow_sending_without_reply': True})
        return tg_event(self.api('sendMessage', params)['message_id'])


class Outbox:
    def __init__(self, vault=VAULT, client=None):
        self.vault = Path(vault)
        self.path = self.vault / '.agents/state/outbox.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.client = client or Telegram()
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS outgoing (
                key TEXT PRIMARY KEY, identity TEXT NOT NULL, channel TEXT NOT NULL,
                body TEXT NOT NULL, parent TEXT, created INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', event TEXT, attempts INTEGER NOT NULL DEFAULT 0,
                error TEXT, next_try INTEGER NOT NULL DEFAULT 0)''')
            db.execute('CREATE INDEX IF NOT EXISTS outgoing_event ON outgoing(event)')
            db.execute('CREATE TABLE IF NOT EXISTS incoming (event TEXT PRIMARY KEY, channel TEXT, payload TEXT, status TEXT DEFAULT "pending")')
        os.chmod(self.path, 0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=60)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def lock(self, name='delivery'):
        with self.path.with_suffix('.' + name + '.lock').open('a') as f:
            lock_exclusive(f)
            yield

    def enqueue(self, identity, channel, body, *, key=None, parent=None):
        if not body.strip():
            raise ValueError('Empty message')
        key = key or hashlib.sha256(f'{identity}\0{channel}\0{parent}\0{time.strftime("%Y-%m-%d")}\0{body}'.encode()).hexdigest()
        with self.db() as db:
            row = db.execute('SELECT * FROM outgoing WHERE key=?', (key,)).fetchone()
            if row and (row['body'], row['channel'], row['identity'], row['parent']) != (body, channel, identity, parent):
                raise ValueError('Outbox key reused with different content')
            db.execute('INSERT OR IGNORE INTO outgoing(key,identity,channel,body,parent,created) VALUES(?,?,?,?,?,?)',
                       (key, identity, channel, body, parent, int(time.time())))
        return key

    def record(self, key):
        with self.db() as db:
            row = db.execute('SELECT * FROM outgoing WHERE key=?', (key,)).fetchone()
            return dict(row) if row else None

    def by_event(self, event):
        """The outgoing row for a delivered message, or None."""
        with self.db() as db:
            row = db.execute('SELECT * FROM outgoing WHERE event=?', (event,)).fetchone()
            return dict(row) if row else None

    def channel_of(self, event):
        row = self.by_event(event)
        return row['channel'] if row else None

    def ancestors(self, event, limit=20):
        """Message ids from `event` up to its thread root, nearest first.

        Buzz kept the whole reply chain in each message's tags; Telegram only
        names the direct parent, so the chain is rebuilt from what this outbox
        sent and received."""
        chain, seen = [], set()
        while event and event not in seen and len(chain) < limit:
            seen.add(event)
            chain.append(event)
            row = self.by_event(event)
            if row:
                event = row['parent']
                continue
            with self.db() as db:
                got = db.execute('SELECT payload FROM incoming WHERE event=?', (event,)).fetchone()
            event = json.loads(got[0]).get('parent') if got else None
        return chain

    def thread_text(self, event, limit=20):
        """Oldest-first bodies of the chain ending at `event`."""
        out = []
        for eid in reversed(self.ancestors(event, limit)):
            row = self.by_event(eid)
            if row:
                out.append(row['body'])
                continue
            with self.db() as db:
                got = db.execute('SELECT payload FROM incoming WHERE event=?', (eid,)).fetchone()
            if got:
                out.append(json.loads(got[0]).get('content', ''))
        return out

    def deliver(self, key):
        with self.lock():
            row = self.record(key)
            if not row:
                raise ValueError('Unknown outbox key')
            if row['event']:
                return row['event']
            try:
                if row['parent'] and not EVENT_RE.fullmatch(row['parent']):
                    raise ValueError('Invalid parent')
                with self.db() as db:
                    db.execute('UPDATE outgoing SET status="sending",attempts=attempts+1 WHERE key=?', (key,))
                mid = self.client.post(row['channel'], row['body'], row['parent'])
                with self.db() as db:
                    db.execute('UPDATE outgoing SET event=?,status="sent",error=NULL WHERE key=?', (mid, key))
                return mid
            except Exception as exc:
                with self.db() as db:
                    db.execute('UPDATE outgoing SET error=?,next_try=? WHERE key=?',
                               (type(exc).__name__, int(time.time()) + min(3600, 30 * 2 ** min(row['attempts'], 6)), key))
                return None

    def flush(self):
        with self.db() as db:
            keys = [r[0] for r in db.execute('SELECT key FROM outgoing WHERE event IS NULL AND next_try<=? ORDER BY created LIMIT 100', (int(time.time()),))]
        for key in keys:
            self.deliver(key)
        with self.db() as db:
            pending = db.execute('SELECT count(*) FROM outgoing WHERE event IS NULL').fetchone()[0]
        (self.path.parent / 'notify_status.json').write_text(json.dumps({'checked_at': int(time.time()), 'pending': pending}) + '\n', encoding="utf-8")
        return pending

    def add_reply(self, msg):
        """Store an owner reply (Buzz message shape) for interactions.py."""
        parent = msg.get('parent')
        channel = (self.channel_of(parent) if parent else None) or 'ask'
        chain = self.ancestors(parent) if parent else []
        # Root first, direct parent last and marked, as Buzz tags were.
        msg['tags'] = [['e', e, '', 'root'] for e in reversed(chain[1:])] + ([['e', parent, '', 'reply']] if parent else [])
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO incoming(event,channel,payload) VALUES(?,?,?)',
                       (msg['id'], channel, json.dumps(msg, ensure_ascii=False)))
        return channel


def send(channel, body, *, identity=None, key=None, parent=None):
    """True means durably queued (not necessarily delivered)."""
    box = Outbox()
    key = box.enqueue(identity or ROUTES.get(channel, channel), channel, body, key=key, parent=parent)
    box.deliver(key)
    return True


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('channel', nargs='?')
    p.add_argument('--flush', action='store_true')
    args = p.parse_args()
    if args.flush:
        print(f'Telegram pending: {Outbox().flush()}')
    elif args.channel:
        send(args.channel, sys.stdin.read())
        print(f'queued to #{args.channel}')
    else:
        p.error('channel or --flush required')


if __name__ == '__main__':
    main()
