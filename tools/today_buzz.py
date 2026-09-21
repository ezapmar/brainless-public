"""Today actions addressed to a Buzz thread, with revision-bound approval."""
import json
from buzz_delivery import Outbox, event_id
from i18n import t, t_list
from today_queue import TodayQueue, atomic_write


def parents(msg):
    return [tag[1] for tag in msg.get('tags', []) if len(tag) > 1 and tag[0] == 'e']


def direct_parent(msg):
    tags = [tag for tag in msg.get('tags', []) if len(tag) > 1 and tag[0] == 'e']
    return next((tag[1] for tag in tags if len(tag) > 3 and tag[3] == 'reply'), tags[-1][1] if tags else None)


def instructions(item):
    return ('\n\n' + t('buzz_interaction.today_help', revision=item['revision']))


def sync_delivery(state, box):
    for item in state['records'].values():
        for intent in item.get('buzz_intents', []):
            record = box.record(intent['key'])
            if not record or not record['event']:
                continue
            mid = record['event']
            item.setdefault('buzz_messages', {})[mid] = intent['revision']
            if intent.get('root'):
                item['buzz_root'] = mid
            if intent.get('approve') and intent['revision'] == item['revision']:
                item['buzz_approval'] = mid


def deliver_intents(queue, state, box):
    # Intents are in the workflow state BEFORE attempting any external write.
    atomic_write(queue.state_path, json.dumps(state, ensure_ascii=False))
    for item in state['records'].values():
        for intent in item.get('buzz_intents', []):
            box.enqueue('tasks', 'tasks', intent['body'], key=intent['key'], parent=intent.get('parent'))
            record = box.record(intent['key'])
            if not record['event']:
                box.deliver(intent['key'])
    sync_delivery(state, box)


def send_queue(queue=None, *, box=None):
    queue = queue or TodayQueue()
    box = box or Outbox(queue.vault)
    with queue.locked() as state:
        queue.finish_apply(state)
        # Snapshot old selected/open items before a new day's selection.
        migrate = [k for k, i in state['records'].items() if i.get('status') in ('open', 'drafted')
                   and i.get('messages') and not i.get('buzz_intents')]
        queue.build(state)
        for key in dict.fromkeys(migrate + state.get('selected', [])):
            item = state['records'][key]
            if item['status'] not in ('open', 'drafted'):
                continue
            if item.get('buzz_sent_revision') == item['revision']:
                continue
            intent = {'key': f"today:{key}:{item['revision']}:root", 'revision': item['revision'], 'root': True, 'approve': True,
                      'body': queue.render_item(item) + instructions(item)}
            item.setdefault('buzz_intents', []).append(intent)
            item['buzz_sent_revision'] = item['revision']
            item.pop('approval_message', None)
            item.pop('chat_id', None)
        deliver_intents(queue, state, box)
    return True


def handle(msg, channel, owner, *, text=None, queue=None, box=None):
    queue = queue or TodayQueue()
    box = box or Outbox(queue.vault)
    if msg.get('pubkey') != owner or channel != box.client.channel('tasks', 'tasks'):
        return False
    mid, parent = event_id(msg), direct_parent(msg)
    if not mid or not parent:
        return False
    with queue.locked() as state:
        queue.finish_apply(state)
        sync_delivery(state, box)
        item = next((i for i in state['records'].values() if any(p in i.get('buzz_messages', {}) for p in parents(msg))), None)
        if not item:
            return False
        if mid in state.get('buzz_handled', []):
            deliver_intents(queue, state, box)
            return True
        text = (msg.get('content', '') if text is None else text).strip()
        action = next((v for v in ('apply', 'edit', 'defer', 'dismiss') if text.casefold() in t_list('today_queue.' + v + '_words')), None)
        try:
            if item['status'] in ('applied', 'deferred', 'dismissed'):
                message = t('today_queue.already_handled')
            elif action == 'apply':
                if parent != item.get('buzz_approval') or item.get('buzz_messages', {}).get(parent) != item['revision']:
                    raise ValueError(t('today_queue.expired'))
                message = queue.act(state, item['id'], action, revision=item['revision'])
                if item['status'] == 'applied':
                    message += '\n' + item['source']
            elif action in ('defer', 'dismiss'):
                item['awaiting'] = action
                item['revision'] += 1
                message = t('today_queue.date_required' if action == 'defer' else 'today_queue.reason_required')
            else:
                message = queue.act(state, item['id'], action or item.get('awaiting', 'answer'), text=text)
                item.pop('awaiting', None)
            if item['status'] == 'drafted' and message != queue.render_item(item):
                message += '\n\n' + queue.render_item(item)
        except ValueError as exc:
            message = str(exc)
            if message == t('today_queue.source_changed') and not item.get('line'):
                fresh = queue.candidate(item['category'], item['source'], item['mode'], item['title'])
                item.update(fingerprint=fresh['fingerprint'], excerpt=fresh['excerpt'], status='open',
                            revision=item['revision'] + 1)
                item.pop('draft', None)
                item.pop('draft_mode', None)
                message += '\n\n' + queue.render_item(item)
        if item['status'] == 'drafted' and queue.render_item(item) not in message:
            message += '\n\n' + queue.render_item(item)
        message += instructions(item) if item['status'] in ('open', 'drafted') else ''
        item.setdefault('buzz_intents', []).append({'key': f'today:reply:{mid}', 'revision': item['revision'],
                                                   'body': message, 'parent': mid, 'approve': item['status'] == 'drafted'})
        state.setdefault('buzz_handled', []).append(mid)
        deliver_intents(queue, state, box)
    return True
