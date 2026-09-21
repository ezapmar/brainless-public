"""In-memory Buzz relay for delivery and workflow regressions."""
import hashlib
import json

OWNER = 'a' * 64


class Relay:
    def __init__(self):
        self.posts = []
        self.offline = False
        self.lose_ack = False
        self.counter = 0

    def credentials(self, identity):
        return 'fixture', hashlib.sha256(identity.encode()).hexdigest()

    def owner(self):
        return OWNER

    def channel(self, name, identity):
        if self.offline:
            raise OSError('offline')
        return 'channel-' + name

    def history(self, identity, channel, since):
        if self.offline:
            raise OSError('offline')
        return [m for m in self.posts if m['channel'] == channel]

    def call(self, identity, args, content=None):
        if self.offline:
            raise OSError('offline')
        if args[:2] == ['messages', 'send']:
            self.counter += 1
            mid = f'{self.counter:064x}'
            m = {'id': mid, 'pubkey': self.credentials(identity)[1], 'content': content,
                 'channel': args[args.index('--channel') + 1], 'created_at': self.counter, 'tags': []}
            if '--reply-to' in args:
                m['tags'] = [['e', args[args.index('--reply-to') + 1], '', 'reply']]
            self.posts.append(m)
            if self.lose_ack:
                self.lose_ack = False
                raise TimeoutError('accepted, acknowledgement lost')
            return {'event_id': mid}
        if args[:2] == ['messages', 'thread']:
            return self.posts
        raise AssertionError(args)


def reply(parent, text, number=100, owner=OWNER, root=None):
    tags = [['e', root, '', 'root']] if root and root != parent else []
    tags.append(['e', parent, '', 'reply'])
    return {'id': f'{number:064x}', 'pubkey': owner, 'content': text, 'tags': tags, 'created_at': number}
