"""Weekly thinking over Buzz. Workflow state and prepared writes survive crashes."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
from pathlib import Path
import sys
import time

from buzz_delivery import Outbox, VAULT, event_id
from i18n import t
from today_buzz import parents, direct_parent
from today_queue import atomic_write

sys.path.insert(0, str(VAULT / '.agents/scripts'))
import thinking_loop as loop


@contextmanager
def locked():
    path = Path(loop.STATE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        state = json.loads(path.read_text()) if path.exists() else {}
        yield state
        persist(state)


def persist(state):
    atomic_write(Path(loop.STATE_FILE), json.dumps(state, ensure_ascii=False, indent=2) + '\n')


def snapshots(state):
    path = loop.target_path(state['kind'], state['target'])
    result = {}
    for value in [path, loop.CALIBRATION if state['kind'] in ('grade', 'predict') else None]:
        if value:
            p = Path(value)
            result[str(p)] = p.read_text() if p.exists() else None
    return result


def sync(state, box):
    for intent in state.get('buzz_intents', []):
        record = box.record(intent['key'])
        if record and record['event']:
            mid = record['event']
            state.setdefault('buzz_messages', {})[mid] = intent['revision']
            if intent.get('root'):
                state['buzz_root'] = mid
            if intent.get('approve') and intent['revision'] == state.get('revision'):
                state['buzz_approval'] = mid


def deliver(state, box):
    persist(state)
    for intent in state.get('buzz_intents', []):
        box.enqueue('thinking', 'thinking', intent['body'], key=intent['key'], parent=intent.get('parent'))
        if not box.record(intent['key'])['event']:
            box.deliver(intent['key'])
    sync(state, box)


def publish(state, body, key, parent=None, root=False, approve=False):
    state.setdefault('buzz_intents', []).append({'key': key, 'body': body, 'parent': parent,
        'root': root, 'approve': approve, 'revision': state['revision']})


def migrate(state, box):
    if state.get('phase') not in ('asked', 'drafted') or state.get('buzz_intents'):
        deliver(state, box)
        return
    state['revision'] = state.get('revision', 0) + 1
    state.setdefault('session', hashlib.sha256(f"{state.get('asked_at')}:{state['kind']}:{state['target']}".encode()).hexdigest()[:20])
    state.pop('message_id', None)
    body = t('thinking_loop.ask_message', question=state['question'])
    if state.get('draft'):
        state['sources'] = snapshots(state)
        body += '\n\n' + loop.preview(state['kind'], state['target'], state['draft']) + t('thinking_loop.draft_footer')
    publish(state, body, 'thinking:root:' + state['session'], root=True, approve=bool(state.get('draft')))
    deliver(state, box)


def ask(dry_run=False, box=None):
    box = box or Outbox()
    with locked() as state:
        if state.get('phase') in ('asked', 'drafted'):
            if dry_run:
                print(state['question'])
                return
            # Preserve the owner's existing pending question and preview at cutover.
            migrate(state, box)
            return
        q = loop.pick_question(state)
        if not q:
            return
        kind, target, question = q
        if dry_run:
            print(t('thinking_loop.ask_message', question=question))
            return
        state.update(phase='asked', kind=kind, target=target, question=question, asked_at=time.time(),
                     draft=None, revision=0, buzz_intents=[], buzz_messages={})
        state.pop('session', None)
        state.pop('buzz_approval', None)
        state['recent_kinds'] = (state.get('recent_kinds', []) + [kind])[-4:]
        migrate(state, box)


def resume_writes(state):
    op = state.get('pending_apply')
    if not op:
        return
    from compile_resources import _is_private
    pending = []
    vault = Path(loop.VAULT).resolve()
    for entry in op['writes']:
        path = Path(entry['path']).resolve()
        if not path.is_relative_to(vault) or _is_private(path):
            raise ValueError('Invalid thinking target')
        rel = path.relative_to(vault).as_posix()
        if not (rel.startswith('Thinking/') or rel.startswith('.wiki/digests/queries/')):
            raise ValueError('Invalid thinking target')
        current = path.read_text() if path.exists() else None
        if current not in (entry['before'], entry['after']):
            raise ValueError(t('buzz_interaction.source_changed'))
        pending.append((path, entry['after']))
    for path, body in pending:
        atomic_write(path, body)
    state['phase'] = 'applied'
    state['applied_files'] = [str(Path(e['path']).resolve().relative_to(vault)) for e in op['writes']]
    state.pop('pending_apply')
    persist(state)


def handle(msg, channel, owner, *, text=None, box=None):
    box = box or Outbox()
    if msg.get('pubkey') != owner or channel != box.client.channel('thinking', 'thinking'):
        return False
    mid, parent = event_id(msg), direct_parent(msg)
    if not mid or not parent:
        return False
    with locked() as state:
        sync(state, box)
        if not any(p in state.get('buzz_messages', {}) for p in parents(msg)):
            return False
        if mid in state.get('buzz_handled', []):
            deliver(state, box)
            return True
        resume_writes(state)
        text = (msg.get('content', '') if text is None else text).strip()
        low = text.casefold()
        approve = False
        try:
            if state.get('phase') not in ('asked', 'drafted'):
                body = t('thinking_loop.applied', files=', '.join(state.get('applied_files', []))) if state.get('phase') == 'applied' else t('thinking_loop.skipped')
            elif low in loop.APPLY_WORDS:
                if state['phase'] != 'drafted' or parent != state.get('buzz_approval') or state['buzz_messages'].get(parent) != state['revision']:
                    raise ValueError(t('today_queue.expired'))
                if snapshots(state) != state.get('sources'):
                    raise ValueError(t('buzz_interaction.source_changed'))
                writes = {}
                loop.apply(state['kind'], state['target'], state['draft'], state.get('answer', ''),
                           collect=writes, stamp='buzz-' + state['session'])
                # A generated seed must never overwrite an existing note.
                if state['kind'] == 'seed' and any(Path(p).exists() for p in writes if Path(p).parent == Path(loop.IDEAS_DIR)):
                    raise ValueError(t('buzz_interaction.source_changed'))
                state['pending_apply'] = {'writes': [{'path': p, 'before': Path(p).read_text() if Path(p).exists() else None, 'after': body}
                                                     for p, body in writes.items()]}
                persist(state)
                resume_writes(state)
                body = t('thinking_loop.applied', files=', '.join(state['applied_files']))
            elif low in loop.CANCEL_WORDS:
                state.update(phase='asked', draft=None, revision=state['revision'] + 1)
                body = t('thinking_loop.cancelled')
            elif low in loop.SKIP_WORDS:
                state.setdefault('skipped', []).append(f"{state['kind']}:{state['target']}")
                state.update(phase='skipped', draft=None, revision=state['revision'] + 1)
                body = t('thinking_loop.skipped')
            else:
                # Store input before a potentially slow model call.
                state['answer'] = text
                state['sources'] = snapshots(state)
                persist(state)
                draft = loop.draft(state['kind'], state['target'], text)
                if not draft:
                    raise RuntimeError('Thinking draft failed; input retained')
                state.update(phase='drafted', draft=draft, revision=state['revision'] + 1)
                body = loop.preview(state['kind'], state['target'], draft) + t('thinking_loop.draft_footer')
                approve = True
        except ValueError as exc:
            body = str(exc)
        publish(state, body, 'thinking:reply:' + mid, parent=mid, approve=approve)
        state.setdefault('buzz_handled', []).append(mid)
        deliver(state, box)
    return True
