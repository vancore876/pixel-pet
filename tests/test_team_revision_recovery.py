"""Real independent PC caches recover ancestry without dropping local writing."""
import copy
import os
import json
import sqlite3
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from notes import NoteService, NoteStore
from settings import AppSettings
from shared_workspace import SharedWorkspace
from team_service import TeamService, shared_note_payload
from team_store import TeamStore


class TeamRevisionRecoveryTests(unittest.TestCase):
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
        for store, notes, team, errors in self.clients:
            team.stop()
        for store, notes, team, errors in self.clients:
            team._thread.join(timeout=2)
            self.assertFalse(team._thread.is_alive())
            notes.stop()
            store.close()
        QTest.qWait(10)
        self.temp.cleanup()

    def wait(self, predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            QTest.qWait(5)
        self.fail('Timed out: ' + '; '.join(client[2].status for client in self.clients))

    def client(self, name):
        base = self.root / name
        store = NoteStore(base / 'notes.json')
        settings = AppSettings(base / 'settings.json')
        notes = NoteService(store, settings, lambda: False)
        team = TeamService(notes, settings, base / 'profile' / 'team.sqlite', can_notify=lambda: False)
        errors = []
        team.error.connect(errors.append)
        self.clients.append((store, notes, team, errors))
        team.configure(str(self.share), name)
        team.timer.stop()
        self.wait(lambda: bool(team.identity()) and not team._busy)
        return self.clients[-1]

    def sync(self, team):
        self.wait(lambda: not team._busy)
        team.pump()
        self.wait(lambda: not team._busy)
        self.app.processEvents()

    def create_three(self, body='Original shared writing'):
        alice, bob, carol = self.client('Alice'), self.client('Bob'), self.client('Carol')
        note = alice[1].save_note(None, 'Customer notebook', body, 0)
        self.sync(alice[2])
        self.sync(bob[2])
        self.sync(carol[2])
        return alice, bob, carol, note['id']

    def all_sync(self, clients, rounds=2):
        for _ in range(rounds):
            for client in clients:
                self.sync(client[2])

    def assert_no_errors(self):
        self.assertEqual([error for client in self.clients for error in client[3]], [])

    def body(self, store, note):
        return ''.join(store.documents.iter_text(note['document_id'])) if note.get('document_id') else note['body']

    def test_three_pcs_preserve_local_edit_when_coworker_deleted_original(self):
        alice, bob, carol, identifier = self.create_three()
        alice[0].edit(identifier, 'Customer notebook', 'Unsent local correction', 0)
        bob[1].delete(identifier)
        self.sync(bob[2])
        self.sync(alice[2])
        self.all_sync((alice, bob, carol))
        for client in (alice, bob, carol):
            notes = client[0].notes
            self.assertEqual(len(notes), 1)
            self.assertEqual(notes[0]['body'], 'Unsent local correction')
            self.assertNotEqual(notes[0]['team_id'], identifier)
        events = SharedWorkspace(self.share).read_events()[0]
        recovered = [event for event in events if event['kind'] == 'note' and event['operation'] == 'put'
                     and event['payload']['body'] == 'Unsent local correction']
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]['parents'], [])
        self.assert_no_errors()

    def test_full_notebook_recovery_retires_original_before_adding_copy(self):
        alice, bob, carol = self.client('Alice'), self.client('Bob'), self.client('Carol')
        original = alice[0].add('Customer notebook', 'Original shared writing', 0)
        original['id'] = identifier = 'f' * 32
        alice[0].save()
        self.all_sync((alice, bob, carol))
        alice[0].edit(identifier, 'Customer notebook', 'Edited writing at notebook capacity', 0)
        bob[1].delete(identifier)
        self.sync(bob[2])
        with patch('team_service.MAX_NOTES', 1):
            self.sync(alice[2])
            self.all_sync((alice, bob, carol))
        for client in (alice, bob, carol):
            self.assertEqual(len(client[0].notes), 1)
            self.assertEqual(client[0].notes[0]['body'], 'Edited writing at notebook capacity')
            self.assertLess(client[0].notes[0]['team_id'], identifier)
        self.assert_no_errors()

    def test_deletion_first_keeps_midflight_edit_and_applies_unrelated_update(self):
        alice = self.client('Alice')
        original = alice[0].add('Protected local note', 'Before worker snapshot', 0)
        other = alice[0].add('Other shared note', 'Before remote update', 0)
        self.sync(alice[2])
        snapshot = copy.deepcopy(alice[0].notes)
        updates = []
        for note in snapshot:
            payload = shared_note_payload(note)
            deleted = note['id'] == original['id']
            if not deleted:
                payload['body'] = 'Unrelated remote update arrives'
            updates.append({'local_id': note['id'], 'entity_id': note['team_id'],
                            'payload': payload, 'revisions': [uuid.uuid4().hex],
                            'deleted': deleted, 'conflict_revision': '', 'original': note})
        alice[0].edit(original['id'], original['title'], 'Saved while the network worker was running', 0)
        result = {'snapshot': {'notes': snapshot}, 'updates': updates, 'restore_id': None,
                  'document_path': str(alice[0].path.parent / 'documents.sqlite')}
        with patch('team_service.MAX_NOTES', 2):
            alice[2]._apply_updates(result)
        self.assertEqual(len(alice[0].notes), 2)
        self.assertEqual(alice[0].find(original['id'])['body'], 'Saved while the network worker was running')
        self.assertEqual(alice[0].find(other['id'])['body'], 'Unrelated remote update arrives')
        self.assert_no_errors()

    def test_full_document_survives_remote_delete_and_recovery_to_all_pcs(self):
        original = ('Original PDF text café\n' * 1600) + 'Final unique sentence'
        edited = original
        alice, bob, carol, identifier = self.create_three(original)
        alice[0].edit(identifier, 'Locally revised PDF reference', alice[0].find(identifier)['body'], 0)
        bob[1].delete(identifier)
        self.sync(bob[2])
        self.sync(alice[2])
        self.all_sync((alice, bob, carol))
        for client in (alice, bob, carol):
            self.assertEqual(len(client[0].notes), 1)
            note = client[0].notes[0]
            self.assertEqual(self.body(client[0], note), edited)
            self.assertEqual(note['body_characters'], len(edited))
        self.assert_no_errors()

    def test_missing_event_cache_replays_ancestry_and_preserves_concurrent_conflict(self):
        alice, bob, carol, identifier = self.create_three()
        alice[0].edit(identifier, 'Customer notebook', 'Alice local version', 0)
        cache = TeamStore(alice[2].profile_path)
        try:
            # The transport already confirmed these files. Restoring an older
            # cache must trigger a complete replay rather than skip them forever.
            with cache._db:
                cache._db.execute('DELETE FROM event_parents WHERE event_id IN (SELECT event_id FROM events WHERE kind=?)', ('note',))
                cache._db.execute('DELETE FROM events WHERE kind=?', ('note',))
        finally:
            cache.close()
        bob[1].save_note(identifier, 'Customer notebook', 'Bob concurrent version', 0)
        self.sync(bob[2])
        self.sync(alice[2])
        self.all_sync((alice, bob, carol))
        for client in (alice, bob, carol):
            self.assertEqual({note['body'] for note in client[0].notes}, {'Alice local version', 'Bob concurrent version'})
            cache = TeamStore(client[2].profile_path)
            try:
                entity = cache.get_entity('note', identifier)
                self.assertTrue(entity['conflict'])
                self.assertEqual(len(entity['revisions']), 2)
                self.assertEqual(len(cache.entities('note')), 1)
            finally:
                cache.close()
        self.assert_no_errors()

    def test_unavailable_ancestry_recovers_copy_without_blocking_other_notes(self):
        alice, bob, carol, identifier = self.create_three()
        alice[0].edit(identifier, 'Customer notebook', 'Local writing with lost ancestry', 0)
        alice[0].find(identifier)['team_revisions'] = [uuid.uuid4().hex]
        alice[0].save()
        # Both directions must keep working while one note is recovered.
        outgoing = alice[0].add('Other local note', 'Still uploads', 0)
        incoming = bob[1].save_note(None, 'Other remote note', 'Still downloads', 0)
        self.sync(bob[2])
        self.sync(alice[2])
        self.all_sync((alice, bob, carol))
        expected = {'Original shared writing', 'Local writing with lost ancestry', 'Still uploads', 'Still downloads'}
        for client in (alice, bob, carol):
            self.assertEqual({note['body'] for note in client[0].notes}, expected)
            self.assertIsNotNone(client[0].find(outgoing['id']))
            self.assertIsNotNone(client[0].find(incoming['id']))
        self.assert_no_errors()

    def test_copied_note_without_any_remote_entity_is_rebound_once(self):
        alice, bob, carol = self.client('Alice'), self.client('Bob'), self.client('Carol')
        note = alice[0].add('Recovered notebook import', 'Copied local writing', 0)
        note.update(team_id=note['id'], team_workspace_id=alice[2].identity()['workspace_id'],
                    team_revisions=[uuid.uuid4().hex], team_due_at=None)
        alice[0].save()
        self.sync(alice[2])
        self.all_sync((alice, bob, carol), rounds=3)
        self.assertEqual(len(alice[0].notes), 1)
        self.assertEqual(alice[0].notes[0]['id'], note['id'])
        self.assertNotEqual(alice[0].notes[0]['team_id'], note['id'])
        for client in (bob, carol):
            self.assertEqual(len(client[0].notes), 1)
            self.assertEqual(client[0].notes[0]['body'], 'Copied local writing')
        self.assert_no_errors()

    def test_pending_document_upload_does_not_overwrite_edit_and_other_notes_sync(self):
        original = 'Original reference summary'
        edited = original + 'Unpublished local text\n' * 1200 + 'End of full text'
        alice, bob, carol, identifier = self.create_three(original)
        alice[0].edit(identifier, 'Customer notebook', edited, 0)
        bob[1].save_note(identifier, 'Remote updated title', original, 0)
        incoming = bob[1].save_note(None, 'Remote independent', 'Remote note arrives', 0)
        self.sync(bob[2])
        outgoing = alice[0].add('Local independent', 'Local note uploads', 0)
        with patch.object(TeamService, '_upload_document', side_effect=OSError('Synthetic network interruption')):
            self.sync(alice[2])
            self.sync(carol[2])
        self.assertEqual(self.body(alice[0], alice[0].find(identifier)), edited)
        self.assertIsNotNone(alice[0].find(incoming['id']))
        self.assertIsNotNone(carol[0].find(outgoing['id']))
        self.all_sync((alice, bob, carol))
        for client in (alice, bob, carol):
            self.assertIn(edited, [self.body(client[0], note) for note in client[0].notes])
        self.assert_no_errors()

    def test_missing_baseline_preserves_local_text_after_remote_deletion(self):
        alice, bob, carol, identifier = self.create_three()
        alice[0].edit(identifier, 'Customer notebook', 'Writing after baseline loss', 0)
        with sqlite3.connect(alice[2].profile_path.with_suffix('.bridge.sqlite')) as db:
            db.execute('DELETE FROM notes WHERE local_id=?', (identifier,))
        bob[1].delete(identifier)
        self.sync(bob[2])
        self.sync(alice[2])
        self.all_sync((alice, bob, carol))
        for client in (alice, bob, carol):
            self.assertEqual([note['body'] for note in client[0].notes], ['Writing after baseline loss'])
        self.assert_no_errors()

    def test_unavailable_delete_ancestry_defers_only_that_record(self):
        alice, bob, carol, identifier = self.create_three()
        alice[0].state['notes'] = []
        alice[0].save()
        with sqlite3.connect(alice[2].profile_path.with_suffix('.bridge.sqlite')) as db:
            db.execute('UPDATE notes SET revisions=? WHERE local_id=?', (json.dumps([uuid.uuid4().hex]), identifier))
        outgoing = alice[0].add('Separate outgoing', 'Unaffected outgoing writing', 0)
        incoming = bob[1].save_note(None, 'Separate incoming', 'Unaffected incoming writing', 0)
        self.sync(bob[2])
        self.sync(alice[2])
        self.sync(carol[2])
        self.assertIsNone(alice[0].find(identifier))
        self.assertIsNotNone(alice[0].find(incoming['id']))
        self.assertIsNotNone(carol[0].find(outgoing['id']))
        self.assertIsNotNone(carol[0].find(identifier))
        self.assertIn('waiting for its shared revisions', alice[2].status)
        self.assert_no_errors()

    def test_cross_record_parent_validation_remains_strict(self):
        alice, bob, carol, identifier = self.create_three()
        cache = TeamStore(alice[2].profile_path)
        try:
            original = cache.get_entity('note', identifier)
            other_id = uuid.uuid4().hex
            payload = copy.deepcopy(original['payload'])
            payload['id'] = other_id
            self.assertFalse(cache.has_revisions('note', other_id, original['revisions']))
            with self.assertRaisesRegex(ValueError, 'current revisions'):
                cache.put_note(payload, original['revisions'])
            self.assertIsNone(cache.get_entity('note', other_id))
        finally:
            cache.close()


if __name__ == '__main__':
    unittest.main()
