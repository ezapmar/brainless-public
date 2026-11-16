"""Telegram outbox: durable, idempotent, routes replies by the message they answer.

Run: python3 -B -m unittest tools.tests.test_notify -v
"""
import tempfile
import unittest
from pathlib import Path

from notify import Outbox, event_id
from tests.chat_fixture import Relay


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.relay = Relay()
        self.box = Outbox(Path(self.tmp.name), self.relay)

    def tearDown(self):
        self.tmp.cleanup()

    def test_same_key_is_sent_once(self):
        key = self.box.enqueue('tasks', 'tasks', 'hello', key='k1')
        self.assertEqual(self.box.deliver(key), 'tg1')
        self.box.enqueue('tasks', 'tasks', 'hello', key='k1')
        self.assertEqual(self.box.deliver(key), 'tg1')
        self.assertEqual(len(self.relay.posts), 1)

    def test_key_reuse_with_other_content_is_refused(self):
        self.box.enqueue('tasks', 'tasks', 'hello', key='k1')
        with self.assertRaises(ValueError):
            self.box.enqueue('tasks', 'tasks', 'other', key='k1')

    def test_offline_send_is_kept_and_retried(self):
        self.relay.offline = True
        key = self.box.enqueue('ops', 'ops', 'alert', key='k')
        self.assertIsNone(self.box.deliver(key))
        self.assertEqual(self.box.record(key)['error'], 'OSError')
        self.relay.offline = False
        self.assertEqual(self.box.deliver(key), 'tg1')

    def test_parent_must_be_a_telegram_id(self):
        key = self.box.enqueue('tasks', 'tasks', 'x', key='k', parent='not-an-id')
        self.assertIsNone(self.box.deliver(key))
        self.assertEqual(self.relay.posts, [])

    def test_reply_is_routed_by_the_channel_of_its_parent(self):
        root = self.box.deliver(self.box.enqueue('thinking', 'thinking', 'question?', key='r'))
        follow = self.box.deliver(self.box.enqueue('thinking', 'thinking', 'draft', key='d', parent=root))
        msg = {'id': 'tg50', 'pubkey': '4242', 'content': 'apply', 'parent': follow}
        self.assertEqual(self.box.add_reply(msg), 'thinking')
        self.assertEqual(msg['tags'], [['e', root, '', 'root'], ['e', follow, '', 'reply']])
        self.assertEqual(self.box.thread_text(follow), ['question?', 'draft'])

    def test_question_without_parent_goes_to_ask(self):
        self.assertEqual(self.box.add_reply({'id': 'tg9', 'content': 'what?', 'parent': None}), 'ask')

    def test_event_id_accepts_only_telegram_ids(self):
        self.assertEqual(event_id({'id': 'tg12'}), 'tg12')
        self.assertIsNone(event_id({'id': 'a' * 64}))


if __name__ == '__main__':
    unittest.main()
