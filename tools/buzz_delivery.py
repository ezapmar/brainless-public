"""Durable Buzz outbox. No Telegram fallback. CLI credentials never enter logs."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import time

VAULT = Path(os.environ.get('BRAINLESS_VAULT') or Path(__file__).resolve().parents[1])
ROUTES = {'ops': 'watchdog', 'tasks': 'tasks', 'radar': 'radar', 'content': 'content',
          'daily': 'briefing', 'inbox': 'inbox', 'thinking': 'thinking', 'crm': 'crm',
          'writing': 'writing', 'narratives': 'narratives'}
# Channels where a live buzz-acp agent answers the owner (install_agent_channel.sh).
# Scripts may post there through the outbox, but the polling reply worker must
# not answer in them, or every owner message would get two replies.
HARNESS_CHANNELS = frozenset({'writing', 'narratives'})


def event_id(value):
    if isinstance(value, dict):
        for key in ('id', 'event_id', 'eventId'):
            v = value.get(key)
            if isinstance(v, str) and re.fullmatch(r'[a-f0-9]{64}', v):
                return v
        for child in value.values():
            v = event_id(child)
            if v:
                return v
    return None


def messages(value):
    if isinstance(value, list):
        return [m for v in value for m in messages(v)]
    if isinstance(value, dict):
        if event_id(value) and 'content' in value and 'pubkey' in value:
            return [value]
        return [m for child in value.values() for m in messages(child)]
    return []


class Buzz:
    def __init__(self, config=None):
        self.config = Path(config or Path.home() / '.config/brainless/buzz')

    def credentials(self, identity):
        if not re.fullmatch(r'[a-z0-9_-]+', identity):
            raise ValueError('Invalid Buzz identity')
        text = (self.config / 'keys' / identity).read_text()
        secret = next((l.split(':', 1)[1].strip() for l in text.splitlines() if l.startswith('Secret key:')), '')
        public = next((l.split(':', 1)[1].strip() for l in text.splitlines() if l.startswith('Public key:')), '')
        if not secret:
            raise ValueError('Buzz identity has no key')
        return secret, public

    def owner(self):
        owner = os.environ.get('BUZZ_OWNER_PUBKEY', '')
        p = self.config / 'owner_pubkey'
        if not owner and p.exists():
            owner = p.read_text().strip()
        if not owner:
            for p in sorted(self.config.glob('*.env')):
                for line in p.read_text().splitlines():
                    if line.startswith('BUZZ_ACP_AGENT_OWNER='):
                        owner = line.split('=', 1)[1].strip().strip('"\'')
                        break
                if owner:
                    break
        if not re.fullmatch(r'[a-f0-9]{64}', owner):
            raise ValueError('Buzz owner is not configured')
        return owner

    def call(self, identity, args, content=None):
        secret, _ = self.credentials(identity)
        relay = os.environ.get('BUZZ_RELAY_URL') or (self.config / 'relay_url').read_text().strip()
        binary = shutil.which('buzz') or str(Path.home() / '.cargo/bin/buzz')
        result = subprocess.run([binary, *args], input=content, text=True, capture_output=True,
                                timeout=60, env={**os.environ, 'BUZZ_PRIVATE_KEY': secret, 'BUZZ_RELAY_URL': relay})
        if result.returncode:
            # Do not include raw stderr: it can contain credentials or user content.
            raise RuntimeError(f'Buzz command failed (exit {result.returncode})')
        return json.loads(result.stdout)

    def channel(self, name, identity):
        cache = self.config / 'channels.json'
        if cache.exists():
            cid = json.loads(cache.read_text()).get(name)
            if cid:
                return cid
        data = self.call(identity, ['channels', 'list'])
        for row in data if isinstance(data, list) else data.get('channels', []):
            if row.get('name') == name:
                return row.get('channel_id') or row['id']
        raise ValueError(f'Buzz channel missing: {name}')

    def history(self, identity, channel, since):
        # Grow the full time window; never advance a cursor across a truncated
        # second. Fail closed if the relay caps results or traffic exceeds budget.
        previous = None
        for limit in (100, 500, 2000, 10000, 50000):
            data = messages(self.call(identity, ['messages', 'get', '--channel', channel,
                            '--since', str(max(0, int(since) - 1)), '--limit', str(limit)]))
            ids = frozenset(event_id(m) for m in data)
            if previous is not None and ids == previous and len(data) >= min(limit, 100):
                raise RuntimeError('Buzz history appears capped; cursor retained')
            if len(data) < limit:
                return sorted(data, key=lambda m: (int(m.get('created_at', 0)), event_id(m)))
            previous = ids
        raise RuntimeError('Buzz history window too large; cursor retained')


class Outbox:
    def __init__(self, vault=VAULT, client=None):
        self.vault = Path(vault)
        self.path = self.vault / '.agents/state/buzz_outbox.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.client = client or Buzz()
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS outgoing (
                key TEXT PRIMARY KEY, identity TEXT NOT NULL, channel TEXT NOT NULL,
                body TEXT NOT NULL, parent TEXT, created INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', event TEXT, attempts INTEGER NOT NULL DEFAULT 0,
                error TEXT, next_try INTEGER NOT NULL DEFAULT 0)''')
            db.execute('CREATE TABLE IF NOT EXISTS incoming (event TEXT PRIMARY KEY, channel TEXT, payload TEXT, status TEXT DEFAULT "pending")')
            db.execute('CREATE TABLE IF NOT EXISTS cursors (channel TEXT PRIMARY KEY, since INTEGER)')
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
            fcntl.flock(f, fcntl.LOCK_EX)
            yield

    def enqueue(self, identity, channel, body, *, key=None, parent=None):
        if not body.strip():
            raise ValueError('Empty Buzz message')
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

    def deliver(self, key):
        with self.lock():
            row = self.record(key)
            if not row:
                raise ValueError('Unknown outbox key')
            if row['event']:
                return row['event']
            marker = '<!-- brainless:' + hashlib.sha256(key.encode()).hexdigest() + ' -->'
            try:
                cid = self.client.channel(row['channel'], row['identity'])
                if row['parent'] and not re.fullmatch('[a-f0-9]{64}', row['parent']):
                    raise ValueError('Invalid Buzz parent')
                if row['status'] == 'sending':
                    # A crash/timeout may have happened AFTER relay acceptance.
                    # Reconcile the exact marker and author before another send.
                    _, public = self.client.credentials(row['identity'])
                    matches = [m for m in self.client.history(row['identity'], cid, row['created'])
                               if m.get('pubkey') == public and marker in m.get('content', '')]
                    if matches:
                        mid = event_id(matches[0])
                        with self.db() as db:
                            db.execute('UPDATE outgoing SET event=?,status="sent",error=NULL WHERE key=?', (mid, key))
                        return mid
                with self.db() as db:
                    db.execute('UPDATE outgoing SET status="sending",attempts=attempts+1 WHERE key=?', (key,))
                args = ['messages', 'send', '--channel', cid, '--content', '-']
                if row['parent']:
                    args += ['--reply-to', row['parent']]
                result = self.client.call(row['identity'], args, row['body'] + '\n\n' + marker)
                mid = event_id(result)
                if not mid:
                    raise RuntimeError('Buzz returned no event id; delivery will be reconciled')
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
        (self.path.parent / 'buzz_delivery_status.json').write_text(json.dumps({'checked_at': int(time.time()), 'pending': pending}) + '\n')
        return pending


def send(channel, body, *, identity=None, key=None, parent=None):
    """True means durably queued (not necessarily delivered)."""
    box = Outbox()
    key = box.enqueue(identity or ROUTES[channel], channel, body, key=key, parent=parent)
    box.deliver(key)
    return True


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('identity', nargs='?')
    p.add_argument('channel', nargs='?')
    p.add_argument('--flush', action='store_true')
    args = p.parse_args()
    if args.flush:
        print(f'Buzz pending: {Outbox().flush()}')
    elif args.identity and args.channel:
        send(args.channel, sys.stdin.read(), identity=args.identity)
        print(f'queued as {args.identity} to #{args.channel}')
    else:
        p.error('identity and channel or --flush required')


if __name__ == '__main__':
    main()
