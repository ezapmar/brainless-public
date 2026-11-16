"""In-memory Telegram chat for delivery and workflow regressions.

Stands in for notify.Telegram. The workflow tests were written against Buzz;
the client surface (owner, channel, post) and the message shape they pass to
the handlers ({id, pubkey, content, tags}) did not change, only the ids did:
Telegram messages are "tg<n>".
"""
OWNER = '4242'


class Relay:
    def __init__(self):
        self.posts = []
        self.offline = False
        self.lose_ack = False
        self.counter = 0

    def owner(self):
        return OWNER

    def channel(self, name, identity=None):
        return name

    def post(self, channel, body, parent=None):
        if self.offline:
            raise OSError('offline')
        self.counter += 1
        mid = f'tg{self.counter}'
        m = {'id': mid, 'pubkey': 'bot', 'content': body, 'channel': channel,
             'created_at': self.counter, 'tags': [['e', parent, '', 'reply']] if parent else []}
        self.posts.append(m)
        if self.lose_ack:
            # Telegram cannot be asked what it accepted: a lost reply means the
            # outbox sends again, so a duplicate notice is the expected cost.
            self.lose_ack = False
            raise TimeoutError('accepted, acknowledgement lost')
        return mid


def reply(parent, text, number=100, owner=OWNER, root=None):
    tags = [['e', root, '', 'root']] if root and root != parent else []
    tags.append(['e', parent, '', 'reply'])
    return {'id': f'tg{number}', 'pubkey': owner, 'content': text, 'tags': tags,
            'created_at': number, 'parent': parent}
