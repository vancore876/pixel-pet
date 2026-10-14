"""Transport failure and late-result regressions with temporary workspaces."""
import copy
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from documents import DocumentBusy, DocumentStore, _LIVE_RESTORES
from notes import NoteService, NoteStore
from settings import AppSettings
from shared_workspace import SharedWorkspace, SharedWorkspaceError
from team_service import TeamService
from team_store import TeamStore


class TeamAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.share = self.root / 'share'
        self.share.mkdir()
        self.clients = []

    def tearDown(self):
        for store, notes, team in self.clients:
            team.stop()
            notes.stop()
            store.close()
        QTest.qWait(10)
        self.temp.cleanup()

    def wait(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            QTest.qWait(5)
        self.fail('Timed out: ' + '; '.join(team.status for _, _, team in self.clients))

    def client(self, name='Audit employee', connect=True):
        folder = self.root / uuid.uuid4().hex
        store = NoteStore(folder / 'notes.json')
        settings = AppSettings(folder / 'settings.json')
        notes = NoteService(store, settings, lambda: False)
        team = TeamService(notes, settings, folder / 'team.sqlite', can_notify=lambda: False)
        self.clients.append((store, notes, team))
        if connect:
            team.configure(str(self.share), name)
            self.wait(lambda: bool(team.identity()) and not team._busy)
        return store, notes, team

    def sync(self, team):
        self.wait(lambda: not team._busy)
        team.pump()
        self.wait(lambda: not team._busy)

    def test_malformed_real_event_does_not_block_later_valid_note(self):
        transport = SharedWorkspace(self.share)
        producer = TeamStore(self.root / 'producer.sqlite')
        try:
            producer.configure(transport.workspace_id, 'Synthetic sender')
            valid = producer.put_note({'id': uuid.uuid4().hex, 'title': 'Synthetic pickup',
                                      'body': 'Test content only', 'created': time.time(),
                                      'repeat_minutes': 0})
            malformed = copy.deepcopy(valid)
            malformed['event_id'] = uuid.uuid4().hex
            malformed['entity_id'] = uuid.uuid4().hex
            malformed['payload']['id'] = malformed['entity_id']
            malformed['payload']['created'] = 'bad date'
            transport.publish_event(malformed)
            transport.publish_event(valid)
        finally:
            producer.close()
        store, _, team = self.client()
        self.assertEqual(store.find(valid['entity_id'])['body'], 'Test content only')
        self.assertIsNone(store.find(malformed['entity_id']))
        self.assertTrue(team.enabled)

    def test_published_before_local_confirmation_is_replayed_then_cleared(self):
        store, notes, team = self.client()
        note = notes.save_note(None, 'Synthetic replay', 'Test content only', 0)
        self.sync(team)
        with sqlite3.connect(team.profile_path) as database:
            event_id = database.execute('SELECT event_id FROM events WHERE kind=? AND entity_id=?',
                                        ('note', note['id'])).fetchone()[0]
            database.execute('UPDATE events SET published=0 WHERE event_id=?', (event_id,))
        self.sync(team)
        with sqlite3.connect(team.profile_path) as database:
            published = database.execute('SELECT published FROM events WHERE event_id=?',
                                         (event_id,)).fetchone()[0]
        self.assertEqual(published, 1)
        self.assertEqual(len(store.notes), 1)

    def test_obsolete_result_releases_document_restore_and_notification_claim(self):
        _, _, team = self.client(connect=False)
        transport = SharedWorkspace(self.share)
        document_path = self.root / 'staged.sqlite'
        documents = DocumentStore(document_path)
        restore_id = documents.begin_restore([], 0)
        employee, entity, occurrence = uuid.uuid4().hex, uuid.uuid4().hex, uuid.uuid4().hex
        token = transport.claim_notification(employee, entity, occurrence)
        team._generation = 2
        team._receive({'generation': 1, 'operation': 'sync', 'restore_id': restore_id,
                       'document_path': str(document_path), '_transport': transport,
                       '_employee_id': employee,
                       'notification': {'entity_id': entity, 'occurrence_id': occurrence,
                                        '_claim_token': token}})
        self.wait(lambda: str(document_path.resolve()) not in _LIVE_RESTORES)
        # Cleanup is asynchronous; its document and share steps complete separately.
        # Let release take the share lock before attempting another claim.
        self.wait(lambda: not list(transport.notifications_path.glob('*.claim.json')))
        reclaimed = []
        def reclaim():
            try:
                claim = transport.claim_notification(employee, entity, occurrence)
            except SharedWorkspaceError:
                return False
            if claim:
                reclaimed.append(claim)
            return bool(claim)
        self.wait(reclaim)
        self.assertNotEqual(reclaimed[0], token)
        # A new restore must work immediately, rather than requiring app restart.
        next_restore = documents.begin_restore([], 0)
        documents.abort_restore(next_restore)

    def test_stopping_does_not_wait_for_a_blocked_network_read(self):
        _, _, team = self.client(connect=False)
        entered, release = threading.Event(), threading.Event()
        original_read = SharedWorkspace.read_events

        def blocked_read(transport, *args, **kwargs):
            entered.set()
            release.wait(timeout=5)
            return original_read(transport, *args, **kwargs)

        with patch.object(SharedWorkspace, 'read_events', blocked_read):
            try:
                team.configure(str(self.share), 'Synthetic employee')
                self.assertTrue(entered.wait(timeout=2))
                started = time.monotonic()
                team.stop()
                self.assertLess(time.monotonic() - started, 1)
            finally:
                release.set()
            self.wait(lambda: not team._thread.is_alive())

    def test_expired_notification_result_is_released_without_showing_popup(self):
        _, _, team = self.client()
        team.can_notify = lambda: True
        transport = SharedWorkspace(self.share)
        employee, entity, occurrence = team.identity()['id'], uuid.uuid4().hex, uuid.uuid4().hex * 2
        token = transport.claim_notification(employee, entity, occurrence)
        presented = []
        team.notification.connect(presented.append)
        team._receive({'generation': team._generation, 'operation': 'sync',
                       'notification': {'id': entity, 'entity_id': entity, 'entity_kind': 'message',
                                        'occurrence_id': occurrence, '_claim_token': token,
                                        '_claim_deadline': time.monotonic() - 1}})
        self.wait(lambda: not list(transport.notifications_path.glob('*.claim.json')))
        self.assertEqual(presented, [])
        self.assertIsNone(team._pending)

    def test_unchanged_sync_preserves_notebook_file(self):
        store, notes, team = self.client()
        notes.save_note(None, 'Synthetic idle sync', 'Test content only', 0)
        self.sync(team)
        before = store.path.stat().st_mtime_ns
        self.sync(team)
        self.assertEqual(store.path.stat().st_mtime_ns, before)

    def test_committed_text_is_preserved_if_activation_is_busy_then_result_is_retired(self):
        store, _, team = self.client(connect=False)
        # Hold asynchronous baseline processing to exercise the commit/cleanup gap.
        team.stop()
        document_path = store.path.parent / 'documents.sqlite'
        documents = DocumentStore(document_path)
        restore_id = documents.begin_restore([], 0)
        source = self.root / 'shared-text.txt'
        text = 'Synthetic customer reference 🐈\n' * 500
        source.write_text(text, encoding='utf-8')
        prepared = documents.prepare_import({'text_file': source, 'restore_id': restore_id,
                                             'title': 'Synthetic reference', 'other_note_characters': 0})
        entity, workspace = uuid.uuid4().hex, uuid.uuid4().hex
        team._identity = {'workspace_id': workspace}
        payload = {'id': entity, 'title': 'Synthetic reference', 'body': prepared['body'],
                   'created': time.time(), 'written': time.time(), 'repeat_minutes': 0,
                   'done': False, 'due_at': None, 'recipients': [],
                   'document': {'sha256': prepared['body_sha256'],
                                'characters': prepared['body_characters']}}
        result = {'generation': 1, 'operation': 'sync', 'snapshot': {'notes': []},
                  'document_path': str(document_path), 'restore_id': restore_id,
                  'updates': [{'local_id': entity, 'entity_id': entity, 'payload': payload,
                               'revisions': [uuid.uuid4().hex], 'deleted': False,
                               'conflict_revision': '', 'document': prepared}]}
        original_finish = DocumentStore.finish_restore

        def busy_after_commit(database, referenced, *args, **kwargs):
            if prepared['document_id'] in referenced and not kwargs.get('validate_only'):
                raise DocumentBusy('Simulated post-JSON activation contention')
            return original_finish(database, referenced, *args, **kwargs)

        with patch.object(DocumentStore, 'finish_restore', busy_after_commit):
            team._apply_updates(result)
            self.assertTrue(store.path.is_file())
            self.assertIsNotNone(store.find(entity))
            TeamService._cleanup_result(result)
            self.assertEqual(''.join(store.documents.iter_text(prepared['document_id'])), text)
        # Once contention ends, ordinary recovery activates the committed text.
        store.documents.finish_restore(store._document_manifest(), store.inline_character_count(), recover=True)
        self.assertEqual(''.join(store.documents.iter_text(prepared['document_id'])), text)


if __name__ == '__main__':
    unittest.main()
