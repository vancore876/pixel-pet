"""Office-server caches stay separate from the shared-folder notebook journal."""
import copy
import os
from pathlib import Path
import tempfile
import time
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ai_privacy import is_shared_note, notes_allowed_for_ai
from notes import NoteService, NoteStore
from settings import AppSettings
from shared_business import SharedBusinessController
from shared_workspace import SharedWorkspace
from team_service import TeamService


class TeamCoexistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='buddy-team-cache-')
        self.root = Path(self.temp.name)
        self.shared = self.root / 'shared'
        self.shared.mkdir()
        self.settings = AppSettings(self.root / 'local' / 'settings.json')
        self.settings.save({'ai_share_notes': True, 'team_share_notes_with_ai': False})
        self.store = NoteStore(self.root / 'local' / 'notes.json')
        self.notes = NoteService(self.store, self.settings, lambda: False)
        self.business = SharedBusinessController(self.notes)
        self.team = TeamService(self.notes, self.settings, self.root / 'profile' / 'team.sqlite',
                                can_notify=lambda: False,
                                exclude_note_ids=lambda: self.business._cache_ids)
        self.errors = []
        self.team.error.connect(self.errors.append)

    def tearDown(self):
        self.team.stop()
        self.business.shutdown()
        self.notes.stop()
        self.store.close()
        self.app.processEvents()
        self.temp.cleanup()

    def wait(self, predicate):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            self.app.processEvents()
            if self.errors:
                self.fail('; '.join(self.errors))
            if predicate():
                return
            QTest.qWait(5)
        self.fail('Timed out: ' + self.team.status)

    def connect(self):
        self.team.configure(str(self.shared), 'Alice')
        self.wait(lambda: bool(self.team.identity()) and not self.team._busy)
        self.sync()

    def sync(self):
        self.wait(lambda: not self.team._busy)
        self.team.pump()
        self.wait(lambda: not self.team._busy)
        self.app.processEvents()

    def record(self, revision=1, identifier=None, title='Server-only customer order'):
        note = self.store.new_note(title, 'Private server customer detail', 30,
                                   time.time() + 3600, time.time(),
                                   details={'kind': 'order', 'customer': 'Private API Customer',
                                            'currency': 'JMD'})
        if identifier is not None:
            note['id'] = identifier
        return {'id': note['id'], 'revision': revision, 'deleted': False, 'entry': note}

    def events(self):
        return SharedWorkspace(self.shared).read_events()[0]

    def apply_server(self, record):
        self.business._apply_record(copy.deepcopy(record))
        self.notes.changed.emit(record['id'])

    def test_server_cache_is_private_and_never_published_as_a_folder_note(self):
        record = self.record()
        self.apply_server(record)
        cached = self.store.find(record['id'])
        self.assertTrue(is_shared_note(cached), 'Server records need the same cloud-sharing protection')
        self.assertEqual(notes_allowed_for_ai([cached], self.settings), [])
        self.connect()
        snapshot = self.team._snapshot()
        self.assertIn(record['id'], snapshot['excluded_ids'])
        self.assertNotIn(record['id'], {note['id'] for note in snapshot['notes']})
        self.assertFalse(any(event['kind'] == 'note' and event['entity_id'] == record['id']
                             for event in self.events()))

    def test_server_refresh_and_logout_do_not_delete_or_publish_folder_notes(self):
        record = self.record()
        self.apply_server(record)
        self.connect()
        local = self.notes.save_note(None, 'Folder customer task', 'Check the pickup desk', 0,
                                     details={'kind': 'order', 'customer': 'Folder Customer'})
        self.sync()
        self.wait(lambda: bool(self.store.find(local['id']).get('team_id')))
        published = self.store.find(local['id'])['team_id']
        revised = self.record(revision=2, identifier=record['id'], title='Updated server-only order')
        self.apply_server(revised)
        self.sync()
        self.assertEqual(self.store.find(record['id'])['title'], 'Updated server-only order')
        self.business.set_session('http://127.0.0.1:8765', '')
        self.sync()
        self.assertIsNone(self.store.find(record['id']))
        self.assertIsNotNone(self.store.find(local['id']))
        notes = [event for event in self.events() if event['kind'] == 'note']
        self.assertTrue(any(event['entity_id'] == published for event in notes))
        self.assertFalse(any(event['entity_id'] == record['id'] for event in notes))
        self.assertFalse(any(event['operation'] == 'delete' for event in notes))

    def test_server_cache_with_a_folder_identity_is_not_overwritten_by_folder_projection(self):
        self.connect()
        local = self.notes.save_note(None, 'Original folder order', 'Original folder content', 0,
                                     details={'kind': 'order'})
        self.sync()
        self.wait(lambda: bool(self.store.find(local['id']).get('team_id')))
        before = [event['event_id'] for event in self.events() if event['kind'] == 'note']
        record = self.record(identifier=local['id'])
        self.apply_server(record)
        self.sync()
        self.assertEqual(self.store.find(record['id'])['title'], record['entry']['title'])
        self.assertEqual(self.store.find(record['id'])['body'], record['entry']['body'])
        self.assertEqual([event['event_id'] for event in self.events() if event['kind'] == 'note'], before)


if __name__ == '__main__':
    unittest.main()
