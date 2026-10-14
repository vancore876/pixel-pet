"""Client synchronization invariants using real notebook persistence and scripted IO."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from notes import NoteStore, NoteService
from settings import AppSettings
from shared_business import SharedBusinessController
from test_work_chat import ScriptedApi

APP = QApplication.instance() or QApplication([])


class SharedBusinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = NoteStore(root / 'notes.json')
        self.service = NoteService(self.store, AppSettings(root / 'settings.json'))
        self.api = ScriptedApi()
        self.sync = SharedBusinessController(self.service, api=self.api)

    def tearDown(self):
        self.sync.shutdown()
        self.service.stop()
        self.store.close()
        self.sync.deleteLater()
        APP.processEvents()
        self.temp.cleanup()

    def record(self, revision=1, identifier=None, **changes):
        note = self.store.new_note('Order', 'Check part', 0, None, 1_800_000_000,
            details={'kind': 'order', 'customer': 'Customer', 'currency': 'JMD', **changes})
        if identifier:
            note['id'] = identifier
        return {'id': note['id'], 'revision': revision, 'deleted': False, 'entry': note,
                'updated_by_username': 'alice', 'updated_at': 1_800_000_000}

    def login(self, records=()):
        self.sync.set_session('http://127.0.0.1:8765', 'disposable-token', {'username': 'alice'})
        self.page(records)

    def page(self, records=()):
        call = self.api.pending('/api/business?', 'GET')
        self.api.finish(call, {'entries': list(records),
            'cursor': records[-1]['revision'] if records else self.sync._cursor, 'has_more': False})

    def save(self, identifier=None):
        results = []
        self.sync.save(identifier, 'Draft', 'Confirm brake pads', 30, None,
                       {'kind': 'order', 'currency': 'JMD'}, lambda n, e: results.append((n, e)))
        return results

    def test_requires_login_and_never_publishes_personal_writing(self):
        self.assertIn('Sign in', self.save()[0][1])
        self.assertFalse(self.api.calls)
        personal = self.store.add('Private writing', 'Personal text', repeat=0)
        self.login()
        self.assertTrue(self.sync.ready)
        self.assertFalse(any(call['method'] == 'PUT' for call in self.api.calls))
        self.sync.set_session(self.api.base_url, '')
        self.assertEqual(self.store.find(personal['id'])['body'], 'Personal text')

    def test_remote_update_preserves_captured_edit_version_and_conflict_draft(self):
        first = self.record()
        self.login([first])
        identifier = first['id']
        self.sync.mark_editing(identifier)
        remote = copy.deepcopy(first)
        remote.update(revision=2)
        remote['entry']['body'] = 'Coworker change'
        self.sync.refresh()
        self.page([remote])
        results = self.save(identifier)
        call = self.api.pending('/api/business/' + identifier, 'PUT')
        self.assertEqual(call['body']['expected_revision'], 1)
        self.assertEqual(call['body']['entry']['body'], 'Confirm brake pads')
        self.api.finish(call, error='Conflict', status=409)
        self.assertIn('draft is preserved', results[0][1])
        self.assertEqual(self.store.find(identifier)['body'], 'Coworker change')

    def test_write_echo_does_not_skip_unseen_changes(self):
        self.login()
        results = self.save()
        call = self.api.pending('/api/business/', 'PUT')
        own = {'id': call['body']['entry']['id'], 'revision': 3, 'deleted': False,
               'entry': call['body']['entry']}
        self.api.finish(call, own)
        self.assertFalse(results[0][1])
        self.assertEqual(self.sync._cursor, 0)
        other = self.record(revision=2)
        self.page([other, own])
        self.assertIsNotNone(self.store.find(other['id']))
        self.assertEqual(self.sync._cursor, 3)

    def test_lost_ack_retries_same_id_with_create_version(self):
        self.login()
        self.save()
        first = self.api.pending('/api/business/', 'PUT')
        identifier = first['body']['entry']['id']
        committed = {'id': identifier, 'revision': 1, 'deleted': False, 'entry': first['body']['entry']}
        self.api.finish(first, error='Connection closed', status=0)
        self.page([committed])
        result = self.save()
        retry = self.api.pending('/api/business/', 'PUT')
        self.assertEqual(retry['body']['entry']['id'], identifier)
        self.assertEqual(retry['body']['expected_revision'], 0)
        self.api.finish(retry, error='Conflict', status=409)
        self.assertTrue(result[0][1])
        self.assertEqual(len(self.store.notes), 1)

    def test_tombstone_logout_and_crash_recovery_keep_personal_notes(self):
        private = self.store.add('Private', 'Keep me', repeat=0)
        business = self.record()
        self.login([business])
        self.sync._apply_record({'id': business['id'], 'revision': 2, 'deleted': True, 'entry': None})
        self.assertIsNone(self.store.find(business['id']))
        another = self.record(revision=3)
        self.sync._apply_record(another)
        second = SharedBusinessController(self.service, api=ScriptedApi())
        self.assertIsNone(self.store.find(another['id']))
        self.assertIsNotNone(self.store.find(private['id']))
        second.shutdown()
        second.deleteLater()

    def test_stale_response_cannot_restore_previous_account_cache(self):
        self.sync.set_session('http://127.0.0.1:8765', 'old-token', {'username': 'alice'})
        old_call = self.api.pending('/api/business?')
        self.sync.set_session(self.api.base_url, '', None)
        row = self.record()
        self.api.finish(old_call, {'entries': [row], 'cursor': 1, 'has_more': False})
        self.assertFalse(self.store.notes)
        self.assertFalse(self.sync.ready)

    def test_capacity_failure_preserves_readable_personal_store(self):
        private = self.store.add('Private', 'Preserved', repeat=0)
        self.login()
        row = self.record()
        with patch('shared_business.MAX_NOTES', 1), self.assertRaisesRegex(ValueError, 'full'):
            self.sync._apply_record(row)
        reloaded = NoteStore(self.store.path)
        self.assertEqual([n['id'] for n in reloaded.notes], [private['id']])
        reloaded.close()

    def test_account_change_releases_pending_editor_once(self):
        self.login()
        results = self.save()
        call = self.api.pending('/api/business/', 'PUT')
        self.sync.set_session(self.api.base_url, '', None)
        self.assertEqual(len(results), 1)
        self.assertIn('account changed', results[0][1])
        self.api.finish(call, error='cancelled', status=0)
        self.assertEqual(len(results), 1)

    def test_repeat_without_explicit_due_is_preserved_on_edit(self):
        row = self.record()
        self.login([row])
        self.sync.mark_editing(row['id'])
        self.save(row['id'])
        call = self.api.pending('/api/business/', 'PUT')
        self.assertAlmostEqual(call['body']['entry']['next_due'] - call['body']['entry']['updated'], 1800)


if __name__ == '__main__':
    unittest.main()
