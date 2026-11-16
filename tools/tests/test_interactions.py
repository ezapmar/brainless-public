"""Owner replies: approvals go to their workflow, the rest get a read-only answer.

Run: python3 -B -m unittest tools.tests.test_interactions -v
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import interactions
from notify import Outbox
from tests.chat_fixture import Relay, OWNER


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.relay = Relay()
        self.box = Outbox(Path(self.tmp.name), self.relay)
        self.root = self.box.deliver(self.box.enqueue('tasks', 'tasks', 'item', key='root'))

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, number, text, parent):
        return self.box.add_reply({'id': f'tg{number}', 'pubkey': OWNER, 'content': text, 'parent': parent})

    def status(self, event):
        with self.box.db() as db:
            return db.execute('SELECT status FROM incoming WHERE event=?', (event,)).fetchone()[0]

    def test_tasks_reply_goes_to_the_today_handler(self):
        self.add(100, 'apply', self.root)
        calls = []
        with patch.dict(interactions.HANDLERS, {'tasks': [lambda m, c, o, **kw: calls.append((m['id'], c, o)) or True]}), \
                patch.object(interactions, 'read_only_answer') as answer:
            self.assertEqual(interactions.process(self.box, OWNER), 0)
        self.assertEqual(calls, [('tg100', 'tasks', OWNER)])
        answer.assert_not_called()
        self.assertEqual(self.status('tg100'), 'done')

    def test_unclaimed_reply_gets_a_read_only_answer(self):
        self.add(100, 'why?', self.root)
        with patch.dict(interactions.HANDLERS, {'tasks': [lambda *a, **kw: False]}), \
                patch.object(interactions, 'read_only_answer') as answer:
            interactions.process(self.box, OWNER)
        answer.assert_called_once()

    def test_failed_reply_is_kept_and_blocks_later_ones(self):
        self.add(100, 'draft please', self.root)
        self.add(101, 'apply', self.root)
        def boom(*a, **kw):
            raise RuntimeError('model down')
        with patch.dict(interactions.HANDLERS, {'tasks': [boom]}):
            self.assertEqual(interactions.process(self.box, OWNER), 1)
        self.assertEqual(self.status('tg100'), 'pending')
        self.assertEqual(self.status('tg101'), 'pending')
        self.assertTrue(any(p['tags'] == [['e', 'tg100', '', 'reply']] for p in self.relay.posts))


if __name__ == '__main__':
    unittest.main()
