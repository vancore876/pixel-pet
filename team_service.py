"""Asynchronous work-team sync and the local notebook bridge.

The shared directory contains immutable events and text blobs. SQLite, employee
identity, delivery state and the notebook always stay on the current computer.
All network access runs on one daemon worker; notebook JSON commits remain on
the Qt thread. A durable baseline reconciles local commits after a crash.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import queue
import sqlite3
import tempfile
import threading
import time
import uuid

from PySide6.QtCore import QObject, QTimer, Signal

from notes import BUSINESS_FIELDS, MAX_NOTES, NoteStore


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def _hash(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _entity_id(note):
    value = note.get('team_id') or note['id']
    if isinstance(value, str) and len(value) == 32 and all(c in '0123456789abcdef' for c in value):
        return value
    return hashlib.sha256(str(value).encode('utf-8')).hexdigest()[:32]


def shared_note_payload(note):
    """Copy only the shared content, assignment and intentional schedule.

    Moving ``next_due`` after a local popup, caches, source paths, document IDs
    and desktop pin positions are deliberately excluded from this projection.
    """
    due = note.get('team_due_at') if note.get('team_id') else note.get('next_due')
    payload = {'id': _entity_id(note), 'title': note['title'], 'body': note['body'],
               'created': note['created'], 'written': note.get('written', note['created']),
               'repeat_minutes': note['repeat_minutes'], 'due_at': due, 'done': note['done'],
               'recipients': copy.deepcopy(note.get('team_recipients', [])),
               **{key: copy.deepcopy(note.get(key)) for key in BUSINESS_FIELDS}}
    if '*' in payload['recipients']:
        payload['recipients'] = []
    if note.get('document_id'):
        payload['document'] = {'sha256': note['body_sha256'], 'characters': note['body_characters']}
    if (note.get('team_notify') is False or note.get('document_source') or note.get('document_id')) and not payload['repeat_minutes'] and due is None:
        payload['notify'] = False
    return payload


class _Baseline:
    """Confirmed local projections, separate from the transport's event cache."""
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS notes(workspace TEXT NOT NULL,local_id TEXT NOT NULL, '
                        'entity_id TEXT NOT NULL,payload TEXT NOT NULL,revisions TEXT NOT NULL,deleted INTEGER NOT NULL, '
                        'PRIMARY KEY(workspace,local_id))')
        if 'conflict_revision' not in {row[1] for row in self.db.execute('PRAGMA table_info(notes)')}:
            self.db.execute("ALTER TABLE notes ADD COLUMN conflict_revision TEXT NOT NULL DEFAULT ''")
        self.db.commit()

    def rows(self, workspace):
        return {r[0]: {'local_id': r[0], 'entity_id': r[1], 'payload': json.loads(r[2]),
                       'revisions': json.loads(r[3]), 'deleted': bool(r[4]), 'conflict_revision': r[5]}
                for r in self.db.execute('SELECT local_id,entity_id,payload,revisions,deleted,conflict_revision FROM notes WHERE workspace=?', (workspace,))}

    def save(self, workspace, local_id, entity_id, payload, revisions, deleted=False, conflict_revision=''):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO notes VALUES(?,?,?,?,?,?,?)',
                            (workspace, local_id, entity_id, _json(payload), _json(revisions), int(deleted), conflict_revision))

    def close(self):
        self.db.close()


class TeamService(QObject):
    notification = Signal(object)
    message_queued = Signal(object)
    changed = Signal()
    status_changed = Signal(str)
    error = Signal(str)
    _completed = Signal(object)

    def __init__(self, note_service, settings=None, local_profile_path=None, parent=None, can_notify=lambda: True,
                 exclude_note_ids=lambda: set()):
        super().__init__(parent)
        self.note_service = note_service
        self.settings = settings
        if local_profile_path is None:
            from config import team_data_directory
            local_profile_path = team_data_directory() / 'team.sqlite'
        profile = Path(local_profile_path)
        from config import _network_location
        if str(profile).startswith(('\\\\', '//')) or _network_location(profile):
            raise ValueError('Keep the employee profile and team cache on this computer, outside the shared folder.')
        self.profile_path = profile / 'team.sqlite' if not profile.suffix else profile
        self.shared_folder = ''
        self.status = 'Work sync is off.'
        self.enabled = False
        self.can_notify = can_notify
        self.exclude_note_ids = exclude_note_ids
        self._identity = {}
        self._roster = []
        self._inbox = []
        self._entities = {}
        self._receipts = {}
        self._own_receipts = {}
        self._pending = None
        self._generation = 0
        self._busy = False
        self._dirty = False
        self._applying = False
        self._stopped = False
        self._cancel = threading.Event()
        self._commands = queue.Queue()
        self._completed.connect(self._receive)
        self.note_service.changed.connect(self._local_changed)
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.pump)
        self._thread = threading.Thread(target=self._run, name='Jeffery work sync', daemon=True)
        self._thread.start()

    def _set_status(self, value):
        if value != self.status:
            self.status = value
            self.status_changed.emit(value)

    def identity(self):
        return copy.deepcopy(self._identity)

    def roster(self):
        return copy.deepcopy(self._roster)

    def inbox(self):
        return copy.deepcopy(self._inbox)

    def entities(self, kind='note'):
        return copy.deepcopy([value for (entity_kind, _), value in self._entities.items() if kind == entity_kind])

    def configure(self, shared_folder, name, existing_employee_id=None):
        if self._stopped:
            return False
        if not isinstance(shared_folder, str) or not shared_folder.strip() or not isinstance(name, str) or not name.strip():
            self.error.emit('Choose a shared folder and enter your employee name.')
            return False
        self._generation += 1
        self._cancel.set()
        self._cancel = threading.Event()
        self.enabled = True
        self.shared_folder = shared_folder.strip()
        self._pending = None
        self._busy = True
        self.note_service.team_notifications_active = True
        try:
            self._protect_shared_notes()
        except (OSError, ValueError) as exc:
            self.enabled = False
            self._busy = False
            self.note_service.team_notifications_active = False
            self.error.emit(f'Could not prepare local notes for work sync: {exc}')
            return False
        self._set_status('Connecting to the shared work folder…')
        self._commands.put(('configure', self._generation, {'folder': self.shared_folder, 'name': name.strip(),
                            'employee_id': existing_employee_id, 'cancel': self._cancel, 'snapshot': self._snapshot()}))
        self.timer.start()
        return True

    def disconnect(self):
        self.enabled = False
        self.timer.stop()
        self._generation += 1
        self._cancel.set()
        self._busy = False
        self._dirty = False
        self.note_service.team_notifications_active = False
        if self._pending:
            self._commands.put(('release', self._generation - 1, self._pending))
        self._pending = None
        self._commands.put(('disconnect', self._generation, {}))
        self._set_status('Work sync is off. Shared notes remain available locally.')
        self.changed.emit()

    def _snapshot(self):
        excluded = set(self.exclude_note_ids())
        return {'notes': copy.deepcopy([note for note in self.note_service.store.notes if note['id'] not in excluded]),
                'excluded_ids': list(excluded),
                'document_path': str(self.note_service.store.path.parent / 'documents.sqlite')}

    def _local_changed(self, _identifier=''):
        if self.enabled and not self._applying:
            try:
                self._protect_shared_notes()
            except (OSError, ValueError) as exc:
                self.error.emit(f'Could not mark a saved work note: {exc}')
            self._dirty = True
            self.pump()

    def _protect_shared_notes(self):
        """Protect work content before later changed subscribers build prompts."""
        excluded = set(self.exclude_note_ids())
        notes = [note for note in self.note_service.store.notes if note.get('team_shared') is not True
                 and note['id'] not in excluded
                 and (not note.get('team_workspace_id') or note['team_workspace_id'] == self._identity.get('workspace_id'))]
        if not notes:
            return
        def change():
            for note in notes:
                note['team_shared'] = True
        self.note_service.store.transaction(change)

    def pump(self):
        if not self.enabled or self._stopped:
            return False
        if self._busy:
            self._dirty = True
            return False
        self._busy = True
        self._dirty = False
        quiet = bool(self.settings and self.settings['quiet_mode'])
        allow_notifications = not quiet and bool(self.can_notify()) and self._pending is None
        self._commands.put(('sync', self._generation, {'snapshot': self._snapshot(), 'cancel': self._cancel,
                            'notify': allow_notifications}))
        return True

    def _action(self, operation, arguments):
        if not self.enabled or self._stopped:
            self.error.emit('Connect to the shared work folder first.')
            return False
        self._commands.put((operation, self._generation, arguments))
        return True

    def send_message(self, title, body, recipients, first_due=None, repeat_minutes=30, customer='', note_id=''):
        return self._action('send', {'title': title, 'body': body, 'recipients': recipients,
                            'first_due': first_due, 'repeat_minutes': repeat_minutes, 'customer': customer, 'note_id': note_id})

    def _notification_kind(self, entity_id, kind=None):
        if kind:
            return kind
        if self._pending and self._pending.get('entity_id') == entity_id:
            return self._pending['entity_kind']
        return 'message' if ('message', entity_id) in self._entities else 'note'

    def acknowledge(self, entity_id, entity_kind=None):
        return self._action('receipt', {'action': 'acknowledge', 'entity_id': entity_id,
                            'entity_kind': self._notification_kind(entity_id, entity_kind)})

    def snooze(self, entity_id, minutes=5, entity_kind=None):
        return self._action('receipt', {'action': 'snooze', 'entity_id': entity_id,
                            'entity_kind': self._notification_kind(entity_id, entity_kind), 'minutes': minutes})

    def mark_read(self, entity_id, entity_kind=None):
        return self._action('receipt', {'action': 'mark_read', 'entity_id': entity_id,
                            'entity_kind': self._notification_kind(entity_id, entity_kind)})

    def mark_note_read(self, local_id):
        note = self.note_service.store.find(local_id)
        if note and note.get('team_id') and note.get('team_workspace_id') == self._identity.get('workspace_id'):
            return self.mark_read(note['team_id'], 'note')
        return False

    def assign_note(self, local_id, recipients):
        if not self.enabled:
            self.error.emit('Connect to the shared work folder before assigning a note.')
            return False
        try:
            if not self.note_service.store.find(local_id):
                raise ValueError('This note no longer exists.')
            if local_id in set(self.exclude_note_ids()):
                raise ValueError('This note is managed by the work server. Use its employee-assignment controls.')
            self.note_service.modify(local_id, team_recipients=list(recipients))
            return True
        except (OSError, ValueError, TypeError) as exc:
            self.error.emit(f'Could not assign this shared note: {exc}')
            return False

    def notification_active(self, notification):
        if not self.enabled or not isinstance(notification, dict):
            return False
        kind = notification.get('entity_kind', 'message')
        entity_id = notification.get('entity_id', notification.get('id'))
        entity = self._entities.get((kind, entity_id))
        if not entity or entity['deleted']:
            return False
        payload = entity['payload']
        if payload.get('done') or payload.get('kind') == 'order' and payload.get('order_status') in ('delivered', 'cancelled', 'canceled'):
            return False
        recipients = payload.get('recipients', [])
        if recipients and '*' not in recipients and self._identity.get('employee_id') not in recipients:
            return False
        receipt = self._own_receipts.get((kind, entity_id), {})
        return not receipt.get('acknowledged') and receipt.get('latest_schedule_action') != 'snooze'

    def note_sync_status(self, local_id):
        note = self.note_service.store.find(local_id)
        if not note:
            return ''
        if not note.get('team_id'):
            return 'Pending sync' if self.enabled else 'Personal note'
        if note.get('team_workspace_id') != self._identity.get('workspace_id'):
            return 'Saved from another work team'
        entity = self._entities.get(('note', note['team_id']))
        if entity and entity['conflict']:
            return 'Conflict: both versions preserved; choose a version to resolve'
        return 'Synced' if self.enabled else 'Shared note saved locally'

    def note_receipt_status(self, local_id):
        note = self.note_service.store.find(local_id)
        if not note or not note.get('team_id'):
            return ''
        rows = self._receipts.get(note['team_id'], [])
        if not rows:
            return ''
        return f"Read by {sum(row['read'] for row in rows)} of {len(rows)} employees"

    def resolve_note_conflict(self, local_id):
        note = self.note_service.store.find(local_id)
        if not note or not note.get('team_id'):
            return False
        revisions = note.get('team_revisions', [])
        return self._action('resolve', {'entity_id': note['team_id'], 'revision': revisions[0] if revisions else ''})

    def mark_notification_presented(self, notification):
        if not isinstance(notification, dict):
            return False
        pending = self._pending
        if not pending or pending.get('occurrence_id') != notification.get('occurrence_id'):
            return False
        self._pending = None
        return self._action('presented', pending)

    def release_notification(self, notification=None):
        pending = self._pending
        if pending and (notification is None or pending.get('occurrence_id') == notification.get('occurrence_id')):
            self._pending = None
            return self._action('release', pending)
        return False

    def stop(self):
        if self._stopped:
            return
        self.timer.stop()
        self._stopped = True
        self._cancel.set()
        self.note_service.team_notifications_active = False
        if self._pending:
            self._commands.put(('release', self._generation, self._pending))
        self._commands.put(('stop', self._generation, {}))
        self._thread.join(timeout=0.5)

    def _run(self):
        from team_store import TeamStore
        from shared_workspace import SharedWorkspace
        state = {'store': None, 'transport': None, 'ledger': None, 'generation': -1, 'pending_results': {}}
        try:
            while True:
                operation, generation, arguments = self._commands.get()
                if operation == 'stop':
                    break
                try:
                    if operation == 'configure':
                        if arguments['cancel'].is_set():
                            continue
                        for pending_result in state['pending_results'].values():
                            self._cleanup_result(pending_result)
                        state['pending_results'].clear()
                        state.update(transport=None, generation=generation)
                        if state['store'] is None:
                            self.profile_path.parent.mkdir(parents=True, exist_ok=True)
                            state['store'] = TeamStore(self.profile_path)
                            state['ledger'] = _Baseline(self.profile_path.with_suffix('.bridge.sqlite'))
                        transport = SharedWorkspace(arguments['folder'])
                        identity = state['store'].configure(transport.workspace_id, arguments['name'], arguments.get('employee_id'))
                        current = state['store'].get_entity('profile', identity['employee_id'])
                        if not current or current['payload'].get('name') != identity['name']:
                            state['store'].publish_profile()
                        state.update(transport=transport, generation=generation)
                        result = self._sync(state, arguments['snapshot'], arguments['cancel'], False)
                    elif operation == 'disconnect':
                        for pending_result in state['pending_results'].values():
                            self._cleanup_result(pending_result)
                        state['pending_results'].clear()
                        state['transport'] = None
                        state['generation'] = generation
                        continue
                    elif operation == 'cleanup':
                        pending_result = state['pending_results'].pop(arguments.get('_cleanup_key'), arguments)
                        self._cleanup_result(pending_result)
                        continue
                    elif generation != state['generation'] or state['store'] is None:
                        continue
                    elif operation == 'sync':
                        if state['transport'] is None:
                            raise OSError('The shared folder is not connected. Check the folder and reconnect.')
                        result = self._sync(state, arguments['snapshot'], arguments['cancel'], arguments.get('notify', False))
                    elif operation == 'baseline':
                        for row in arguments['rows']:
                            state['ledger'].save(state['store'].identity()['workspace_id'], **row)
                        if arguments.get('abort_restore'):
                            from documents import DocumentStore
                            DocumentStore(arguments['document_path']).abort_restore(arguments['abort_restore'])
                        pending_result = state['pending_results'].get(arguments.get('_cleanup_key'))
                        if pending_result:
                            pending_result['restore_id'] = None
                            if not pending_result.get('notification'):
                                state['pending_results'].pop(arguments['_cleanup_key'], None)
                        continue
                    elif operation == 'send':
                        event = state['store'].send_message(**arguments)
                        result = self._cached(state)
                        result['queued_message'] = event
                    elif operation == 'receipt':
                        action = arguments.pop('action')
                        getattr(state['store'], action)(state['store'].identity()['employee_id'], **arguments)
                        result = self._cached(state)
                    elif operation == 'resolve':
                        state['store'].resolve_entity('note', arguments['entity_id'], arguments['revision'])
                        result = self._cached(state)
                    elif operation in ('presented', 'release'):
                        identity = state['store'].identity()
                        if operation == 'presented':
                            state['store'].mark_presented(identity['employee_id'], arguments['entity_kind'],
                                arguments['entity_id'], arguments['occurrence_id'], time.time())
                            if arguments.get('_claim_token') and state['transport']:
                                try:
                                    state['transport'].complete_notification(identity['employee_id'], arguments['entity_id'],
                                        arguments['occurrence_id'], arguments['_claim_token'])
                                except Exception:
                                    pass  # The durable local receipt remains queued for reconnect.
                        elif arguments.get('_claim_token') and state['transport']:
                            state['transport'].release_notification(identity['employee_id'], arguments['entity_id'],
                                arguments['occurrence_id'], arguments['_claim_token'])
                        pending_result = state['pending_results'].get(arguments.get('_cleanup_key'))
                        if pending_result:
                            pending_result.pop('notification', None)
                            if not pending_result.get('restore_id'):
                                state['pending_results'].pop(arguments['_cleanup_key'], None)
                        result = self._cached(state)
                    else:
                        continue
                    result.update(generation=generation, operation=operation)
                    if result.get('restore_id') or result.get('notification'):
                        result['_cleanup_key'] = uuid.uuid4().hex
                        result['_transport'] = state['transport']
                        result['_employee_id'] = state['store'].identity()['employee_id']
                        if result.get('notification'):
                            result['notification']['_cleanup_key'] = result['_cleanup_key']
                        state['pending_results'][result['_cleanup_key']] = result
                    if operation in ('configure', 'sync') and arguments['cancel'].is_set():
                        self._cleanup_result(result)
                        state['pending_results'].pop(result.get('_cleanup_key'), None)
                        continue
                    self._completed.emit(result)
                except Exception as exc:
                    self._completed.emit({'generation': generation, 'operation': operation,
                                          'error': f'Work sync: {exc}'})
        finally:
            for pending_result in state['pending_results'].values():
                self._cleanup_result(pending_result)
            if state['store'] is not None:
                state['store'].close()
            if state['ledger'] is not None:
                state['ledger'].close()

    @staticmethod
    def _cleanup_result(result):
        """Discard uncommitted streamed text and claims from obsolete workers."""
        if result.get('restore_id') and result.get('document_path'):
            try:
                from documents import DocumentStore
                DocumentStore(result['document_path']).abort_restore(result['restore_id'])
            except (OSError, ValueError):
                pass  # Durable restore recovery retains the original text.
        notification = result.get('notification')
        transport = result.get('_transport')
        if notification and notification.get('_claim_token') and transport:
            try:
                transport.release_notification(result['_employee_id'], notification['entity_id'],
                    notification['occurrence_id'], notification['_claim_token'])
            except Exception:
                pass  # A disconnected share's claim expires automatically.

    @staticmethod
    def _cached(state):
        store = state['store']
        identity = store.identity()
        entities = [entity for kind in ('note', 'message') for entity in store.entities(kind)]
        roster = store.roster()
        receipts = {}
        own_receipts = {}
        for entity in entities:
            own_receipts[(entity['kind'], entity['id'])] = store.receipt(identity['employee_id'], entity['kind'], entity['id'])
            if entity['kind'] == 'note':
                recipients = entity['payload'].get('recipients', [])
                receipts[entity['id']] = [store.receipt(row['id'], 'note', entity['id'])
                                          for row in roster if not recipients or '*' in recipients or row['id'] in recipients]
        return {'identity': {**identity, 'id': identity['employee_id']}, 'roster': roster,
                'inbox': store.inbox(identity['employee_id']), 'entities': entities, 'receipts': receipts,
                'own_receipts': own_receipts}

    def _sync(self, state, snapshot, cancel, notify):
        from shared_workspace import SharedWorkspaceError
        store, transport, ledger = state['store'], state['transport'], state['ledger']
        workspace = store.identity()['workspace_id']
        warnings = []
        online = True
        try:
            events, incoming_warnings = transport.read_events()
            warnings.extend(str(w) for w in incoming_warnings)
            for event in events:
                if cancel.is_set():
                    raise RuntimeError('The previous work connection was canceled.')
                try:
                    store.ingest_event(event)
                except ValueError as exc:
                    warnings.append(str(exc))
        except (OSError, SharedWorkspaceError) as exc:
            online = False
            warnings.append(f'Shared folder unavailable: {exc}. Changes remain queued locally.')
        notes = {n['id']: n for n in snapshot['notes']
                 if not n.get('team_workspace_id') or n['team_workspace_id'] == workspace}
        baseline = ledger.rows(workspace)
        for local_id, note in notes.items():
            if cancel.is_set():
                raise RuntimeError('The previous work connection was canceled.')
            payload = shared_note_payload(note)
            previous = baseline.get(local_id)
            if previous and not previous['deleted'] and payload == previous['payload']:
                continue
            current = store.get_entity('note', payload['id'])
            matching = next((variant for variant in current['variants'] if not variant['deleted'] and variant['payload'] == payload), None) if current else None
            if matching:
                ledger.save(workspace, local_id, payload['id'], payload, [matching['revision']],
                            conflict_revision=note.get('team_conflict_revision', ''))
                continue
            if current and current['deleted']:
                # A tombstone must not resurrect a stale notebook. Keep a
                # genuinely new local edit as an independent note instead.
                if previous and payload != previous['payload']:
                    replacement = hashlib.sha256((local_id + _hash(payload)).encode()).hexdigest()[:32]
                    payload = {**payload, 'id': replacement}
                    warnings.append('An edited note was also deleted by another employee. Your edited version was preserved as a separate note.')
                else:
                    continue
            if payload.get('document'):
                if not online:
                    continue
                try:
                    self._upload_document(transport, snapshot['document_path'], note, cancel)
                except (OSError, ValueError, SharedWorkspaceError) as exc:
                    warnings.append(f"{note['title']}: full document sync is pending ({exc}).")
                    continue
            parents = note.get('team_revisions') if note.get('team_id') == payload['id'] else []
            if previous and not parents:
                parents = previous['revisions']
            event = store.put_note(payload, base_revisions=parents)
            ledger.save(workspace, local_id, payload['id'], payload, [event['event_id']],
                        conflict_revision=note.get('team_conflict_revision', ''))
        for local_id, previous in baseline.items():
            if local_id not in notes and not previous['deleted']:
                if local_id in snapshot.get('excluded_ids', []):
                    continue  # API-owned cache removal/logout is never a team deletion.
                if previous['conflict_revision']:
                    warnings.append('A conflicting note copy was preserved. Choose a version to resolve the conflict before removing its copy.')
                    continue
                current = store.get_entity('note', previous['entity_id'])
                if current and not current['deleted']:
                    event = store.delete_note(previous['entity_id'], base_revisions=previous['revisions'])
                    revisions = [event['event_id']]
                else:
                    revisions = current['revisions'] if current else previous['revisions']
                ledger.save(workspace, local_id, previous['entity_id'], previous['payload'], revisions, True)
        if online:
            try:
                for event in store.pending_events():
                    if cancel.is_set():
                        raise RuntimeError('The previous work connection was canceled.')
                    # False means the exact immutable event was already
                    # published before a crash; it is still a successful replay.
                    transport.publish_event(event)
                    store.mark_published(event['event_id'])
            except (OSError, SharedWorkspaceError) as exc:
                online = False
                warnings.append(f'Shared folder unavailable: {exc}. Changes remain queued locally.')
        result = self._cached(state)
        result['snapshot'] = snapshot
        result['updates'], result['restore_id'] = self._prepare_updates(state, snapshot, cancel, warnings)
        result['document_path'] = snapshot['document_path']
        result['status'] = ('Connected. Notes and employee reminders are synced.' if online else
                            'Offline. Saved changes will sync when the shared folder returns.')
        if warnings:
            result['status'] = warnings[0]
        result['warnings'] = warnings
        if notify and not cancel.is_set():
            employee = store.identity()['employee_id']
            for candidate in store.due_notifications(employee, time.time()):
                claim_deadline = time.monotonic() + 30
                token = None
                if online:
                    try:
                        token = transport.claim_notification(employee, candidate['entity_id'], candidate['occurrence_id'])
                    except SharedWorkspaceError:
                        online = False
                if token or not online:
                    names = {row['id']: row['name'] for row in result['roster']}
                    result['notification'] = {**candidate, 'author_name': names.get(candidate['author_id'], 'Employee'),
                                              '_claim_token': token, '_claim_deadline': claim_deadline if online else float('inf'),
                                              '_offline': not online}
                    break
        return result

    def _upload_document(self, transport, document_path, note, cancel):
        from documents import DocumentStore
        with tempfile.TemporaryDirectory(prefix='team-document-', dir=self.profile_path.parent) as folder:
            target = Path(folder) / 'text.txt'
            DocumentStore(document_path).export_document(note['document_id'], target, cancel=cancel)
            uploaded = transport.upload_blob(target, expected_characters=note['body_characters'], cancel=cancel)
            if uploaded['sha256'] != note['body_sha256']:
                raise ValueError('The full document checksum changed; its note was not shared.')

    def _prepare_updates(self, state, snapshot, cancel, warnings):
        from documents import DocumentStore
        store = state['store']
        workspace = store.identity()['workspace_id']
        locals_by_id = {note['id']: note for note in snapshot['notes']}
        linked = {}
        documents_by_hash = {}
        for note in snapshot['notes']:
            if note.get('team_workspace_id') == workspace:
                linked[(note.get('team_id'), note.get('team_conflict_revision', ''))] = note
            if note.get('document_id'):
                documents_by_hash[(note['body_sha256'], note['body_characters'])] = note
        rows = []
        active_conflicts = set()
        for entity in store.entities('note'):
            variants = entity['variants']
            winner = max(variants, key=lambda variant: (variant['deleted'], variant['revision']))
            for variant in variants:
                if variant is not winner and (not entity['conflict'] or variant['deleted']):
                    continue
                conflict_revision = '' if variant is winner else variant['revision']
                if conflict_revision:
                    active_conflicts.add((entity['id'], conflict_revision))
                original = linked.get((entity['id'], conflict_revision))
                local_id = original['id'] if original else entity['id'] if not conflict_revision else hashlib.sha256(
                    f"{entity['id']}:{conflict_revision}".encode()).hexdigest()[:32]
                if original is None and not conflict_revision:
                    original = next((note for note in snapshot['notes'] if _entity_id(note) == entity['id']
                                     and not note.get('team_conflict_revision')
                                     and (not note.get('team_workspace_id') or note['team_workspace_id'] == workspace)), None)
                    if original:
                        local_id = original['id']
                rows.append({'local_id': local_id, 'entity_id': entity['id'], 'payload': variant['payload'],
                             'revisions': [variant['revision']], 'deleted': variant['deleted'],
                             'conflict_revision': conflict_revision, 'original': original})
        # Resolved conflict copies are retired only after a descendant has
        # consumed the old heads; their preserved event bodies remain durable.
        for note in snapshot['notes']:
            branch = note.get('team_conflict_revision')
            if branch and note.get('team_workspace_id') == workspace and (note['team_id'], branch) not in active_conflicts:
                rows.append({'local_id': note['id'], 'entity_id': note['team_id'], 'payload': shared_note_payload(note),
                             'revisions': note.get('team_revisions', []), 'deleted': True,
                             'conflict_revision': branch, 'original': note})
        needs_import = []
        retired = set()
        for row in rows:
            document = row['payload'].get('document') if not row['deleted'] else None
            if document:
                reusable = documents_by_hash.get((document['sha256'], document['characters']))
                if reusable:
                    row['document'] = {key: reusable[key] for key in ('document_id', 'body_characters', 'body_sha256', 'body')}
                else:
                    needs_import.append(row)
                    old = row['original']
                    if old and old.get('document_id'):
                        retired.add(old['document_id'])
        restore_id = None
        if needs_import:
            documents = DocumentStore(snapshot['document_path'])
            other_inline = sum(len(note['body']) for note in snapshot['notes'] if not note.get('document_id'))
            restore_id = documents.begin_restore(retired, other_inline)
            try:
                for row in needs_import:
                    descriptor = row['payload']['document']
                    with tempfile.TemporaryDirectory(prefix='team-download-', dir=self.profile_path.parent) as folder:
                        target = Path(folder) / 'text.txt'
                        state['transport'].download_blob(descriptor['sha256'], target, descriptor['characters'], cancel=cancel)
                        prepared = documents.prepare_import({'text_file': target, 'title': row['payload'].get('title', ''),
                            'other_note_characters': other_inline, 'restore_id': restore_id}, cancel=cancel)
                    if (prepared['body_sha256'], prepared['body_characters']) != (descriptor['sha256'], descriptor['characters']):
                        raise ValueError('The shared document checksum or length does not match.')
                    row['document'] = prepared
            except Exception as exc:
                documents.abort_restore(restore_id)
                restore_id = None
                warnings.append(f'Full shared documents could not be downloaded. Existing local text was preserved: {exc}')
                rows = [row for row in rows if row not in needs_import]
        return rows, restore_id

    def _receive(self, result):
        if self._stopped or result['generation'] != self._generation or not self.enabled:
            if result.get('restore_id') or result.get('notification'):
                self._commands.put(('cleanup', result['generation'], result))
            return
        if result['operation'] in ('configure', 'sync'):
            self._busy = False
        if result.get('error'):
            self._set_status(result['error'])
            self.error.emit(result['error'])
            return
        self._identity = result.get('identity', self._identity)
        self._roster = result.get('roster', self._roster)
        self._inbox = result.get('inbox', self._inbox)
        if 'entities' in result:
            self._entities = {(value['kind'], value['id']): value for value in result['entities']}
        self._receipts = result.get('receipts', self._receipts)
        self._own_receipts = result.get('own_receipts', self._own_receipts)
        if result.get('status'):
            self._set_status(result['status'])
        if 'updates' in result:
            self._apply_updates(result)
        self.changed.emit()
        if result.get('queued_message'):
            self.message_queued.emit(copy.deepcopy(result['queued_message']))
        notification = result.get('notification')
        if notification:
            quiet = bool(self.settings and self.settings['quiet_mode'])
            lease_valid = notification.get('_claim_deadline', float('inf')) > time.monotonic()
            if not quiet and self.can_notify() and self._pending is None and lease_valid:
                self._pending = notification
                self.notification.emit(copy.deepcopy(notification))
            else:
                self._commands.put(('release', self._generation, notification))
                if not lease_valid:
                    self._dirty = True
        if result['operation'] != 'sync' or self._dirty:
            self.pump()

    def _apply_updates(self, result):
        store = self.note_service.store
        before = {note['id']: note for note in result['snapshot']['notes']}
        baseline_rows = []
        affected = set()
        retired = set()
        committed = False
        previous = copy.deepcopy(store.state)
        self._applying = True
        try:
            def change():
                for row in result['updates']:
                    current = store.find(row['local_id'])
                    original = before.get(row['local_id'])
                    if original is not None and (current is None or shared_note_payload(current) != shared_note_payload(original)):
                        continue  # An editor commit while the network worker ran.
                    if original is None and current is not None:
                        continue
                    if row['deleted']:
                        if current:
                            if current.get('document_id'):
                                retired.add(current['document_id'])
                            store.state['notes'] = [note for note in store.notes if note['id'] != row['local_id']]
                            affected.add(row['local_id'])
                        baseline_rows.append({key: row[key] for key in ('local_id', 'entity_id', 'payload', 'revisions', 'deleted', 'conflict_revision')})
                        continue
                    payload = row['payload']
                    data = {**(current or {}), 'id': row['local_id'], 'title': payload.get('title', ''), 'body': payload.get('body', ''),
                            'created': payload.get('created', time.time()), 'written': payload.get('written', payload.get('created', time.time())),
                            'repeat_minutes': payload.get('repeat_minutes', 30), 'done': payload.get('done', False),
                            **{key: payload.get(key) for key in BUSINESS_FIELDS},
                            'next_due': None, 'unannounced': False, 'source': current.get('source', '') if current else '',
                            'document_source': current.get('document_source', '') if current else '',
                            'team_id': row['entity_id'], 'team_workspace_id': self._identity['workspace_id'],
                            'team_revisions': row['revisions'], 'team_recipients': payload.get('recipients', []),
                            'team_due_at': payload.get('due_at'), 'team_conflict_revision': row['conflict_revision'],
                            'team_notify': payload.get('notify') is not False}
                    for key in ('document_id', 'body_characters', 'body_sha256'):
                        data.pop(key, None)
                    if row.get('document'):
                        data.update({key: row['document'][key] for key in ('document_id', 'body_characters', 'body_sha256', 'body')})
                    note = NoteStore.validate_note(data)
                    if note is None:
                        raise ValueError('A shared note has no valid title or body.')
                    if current and current.get('document_id') != note.get('document_id'):
                        retired.add(current.get('document_id'))
                    if current:
                        if current != note:
                            current.clear()
                            current.update(note)
                            affected.add(note['id'])
                    else:
                        if len(store.notes) >= MAX_NOTES:
                            raise ValueError('Shared notes exceed the local 2,000-note notebook limit. Export or remove older notes.')
                        store.notes.append(note)
                        affected.add(note['id'])
                    baseline_rows.append({key: row[key] for key in ('local_id', 'entity_id', 'payload', 'revisions', 'deleted', 'conflict_revision')})
            if result.get('restore_id'):
                # Open the local document store so its normal notebook commit
                # validates and activates this staged restore atomically.
                _ = store.documents
            change()
            # Immutable events are polled frequently. Idle polls must not
            # rewrite a potentially 20 MB notebook on the Qt thread.
            if affected or result.get('restore_id'):
                store.save()
            committed = True
            # The JSON commit may be durable even when SQLite activation is
            # temporarily deferred. The worker shares this result object;
            # immediately stop treating its now-referenced text as abortable.
            result['restore_id'] = None
            if retired:
                store._discard_unreferenced(retired)
            if affected:
                self.note_service.changed.emit('')
        except (OSError, ValueError, sqlite3.Error) as exc:
            store.state = previous
            baseline_rows.clear()
            self.error.emit(f'Shared notes could not be saved locally; original notes were preserved: {exc}')
            self._set_status(f'Shared notebook sync needs attention: {exc}')
        finally:
            self._applying = False
            self._commands.put(('baseline', self._generation, {'rows': baseline_rows, 'document_path': result['document_path'],
                                'abort_restore': result.get('restore_id') if not committed else None,
                                '_cleanup_key': result.get('_cleanup_key')}))
