"""Office-server caches stay private beside a shared-folder notebook."""
import copy
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ai_privacy import note_allowed_for_ai
from notes import NoteService, NoteStore
from server_business_schema import validate_business_entry
from settings import AppSettings
from shared_business import SharedBusinessController
from shared_workspace import SharedWorkspace
from team_service import TeamService
from test_work_chat import ScriptedApi


APP = QApplication.instance() or QApplication([])


class TeamCoexistenceAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.share = self.root / 'share'
        self.share.mkdir()
        self.clients = []

    def tearDown(self):
        for store, service, team, business, _ in self.clients:
            team.stop()
            business.shutdown()
            service.stop()
            store.close()
        APP.processEvents()
        QTest.qWait(20)
        self.temp.cleanup()

    def wait(self, predicate):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            APP.processEvents()
            if predicate():
                return
            QTest.qWait(5)
        self.fail('Timed out waiting for a temporary office workspace')

    def client(self, name, connect=False):
        folder = self.root / name
        store = NoteStore(folder / 'notes.json')
        settings = AppSettings(folder / 'settings.json')
        service = NoteService(store, settings, can_notify=lambda: True)
        api = ScriptedApi()
        business = SharedBusinessController(service, api=api)
        team = TeamService(service, settings, folder / 'team.sqlite',
                           can_notify=lambda: False,
                           exclude_note_ids=lambda: business._reserved_cache_ids)
        service.team_excluded_note_ids = lambda: business._cache_ids
        client = (store, service, team, business, api)
        self.clients.append(client)
        if connect:
            self.connect(team, name)
        return client

    def connect(self, team, name):
        self.assertTrue(team.configure(str(self.share), name))
        self.wait(lambda: bool(team.identity()) and not team._busy)

    def sync(self, team):
        self.wait(lambda: not team._busy)
        team.pump()
        self.wait(lambda: not team._busy)
        APP.processEvents()

    def api_record(self, store, **details):
        entry = store.new_note('API pickup', 'Office-server customer details', 30,
                               time.time() - 60, time.time(),
                               details={'kind': 'order', 'customer': 'Synthetic customer',
                                        'currency': 'JMD', **details})
        return {'id': entry['id'], 'revision': 1, 'deleted': False,
                'entry': entry}

    def login(self, business, api, record):
        business.set_session('http://127.0.0.1:8765', 'synthetic-token',
                             {'username': 'Synthetic employee'})
        api.finish(api.pending('/api/business?', 'GET'),
                   {'entries': [record], 'cursor': 1, 'has_more': False})
        self.assertTrue(business.ready)

    def test_api_customer_cache_requires_work_ai_opt_in(self):
        store, _, _, business, api = self.client('alice')
        record = self.api_record(store)
        self.login(business, api, record)
        note = store.find(record['id'])
        self.assertFalse(note_allowed_for_ai(note, business.service.settings))
        business.service.settings.values['team_share_notes_with_ai'] = True
        self.assertTrue(note_allowed_for_ai(note, business.service.settings))

    def test_editing_private_api_cache_still_passes_real_server_schema(self):
        store, _, _, business, api = self.client('alice')
        record = self.api_record(store)
        self.login(business, api, record)
        business.mark_editing(record['id'])
        self.assertTrue(business.save(record['id'], 'Edited pickup', 'Confirm fitment',
                                      30, time.time() + 60,
                                      {'kind': 'order', 'customer': 'Synthetic customer'},
                                      lambda *_: None))
        call = api.pending('/api/business/' + record['id'], 'PUT')
        valid = validate_business_entry(call['body']['entry'], record['id'])
        self.assertEqual(valid['title'], 'Edited pickup')
        self.assertFalse(note_allowed_for_ai(store.find(record['id']), business.service.settings))

    def test_completing_private_api_cache_still_passes_real_server_schema(self):
        store, _, _, business, api = self.client('alice')
        record = self.api_record(store)
        self.login(business, api, record)
        self.assertTrue(business.complete(record['id'], True, lambda *_: None))
        call = api.pending('/api/business/' + record['id'], 'PUT')
        valid = validate_business_entry(call['body']['entry'], record['id'])
        self.assertTrue(valid['done'])
        self.assertEqual(valid['order_status'], 'delivered')

    def test_api_cache_is_not_published_on_folder_connect_edit_or_logout(self):
        store, service, team, business, api = self.client('alice')
        record = self.api_record(store)
        self.login(business, api, record)
        self.connect(team, 'Alice')
        shared = service.save_note(None, 'Folder pickup', 'Shared-folder customer', 0,
                                   details={'kind': 'order', 'currency': 'JMD'})
        self.sync(team)
        record = copy.deepcopy(record)
        record['revision'] = 2
        record['entry']['body'] = 'Changed only on the office server'
        business._apply_record(record)
        service.changed.emit(record['id'])
        self.sync(team)
        business.set_session(api.base_url, '', None)
        self.sync(team)
        events = SharedWorkspace(self.share).read_events()[0]
        self.assertTrue(any(event['kind'] == 'note' and event['entity_id'] == shared['id']
                            for event in events))
        self.assertFalse(any(event['kind'] == 'note' and event['entity_id'] == record['id']
                             for event in events))
        self.assertIsNone(store.find(record['id']))
        self.assertIsNotNone(store.find(shared['id']))

    def test_folder_orders_preserve_auto_part_details_between_computers(self):
        _, service, team, _, _ = self.client('alice', connect=True)
        receiver, _, remote, _, _ = self.client('bob', connect=True)
        details = {'kind': 'order', 'currency': 'JMD', 'customer': 'Synthetic customer',
                   'vehicle': '2015 Toyota Corolla', 'registration': 'TEST-1',
                   'vin': 'MANUALLY ENTERED', 'priority': 'urgent', 'order_type': 'quote',
                   'payment_received_cents': 12345,
                   'checklist': [{'id': 'part-one', 'text': 'Brake pads', 'quantity': 2,
                                  'done': False, 'part_number': 'PAD-1', 'supplier': 'Synthetic supplier',
                                  'bin_location': 'A-2', 'unit_price_cents': 54321,
                                  'stock_status': 'to_order'}]}
        note = service.save_note(None, 'Folder quote', 'Confirm part fitment', 0,
                                  details=details)
        self.sync(team)
        self.sync(remote)
        incoming = receiver.find(note['id'])
        self.assertIsNotNone(incoming)
        for field in details:
            self.assertEqual(incoming[field], note[field], field)

    def test_api_customer_reminder_still_runs_while_folder_scheduler_is_active(self):
        store, service, team, business, api = self.client('alice')
        record = self.api_record(store)
        self.login(business, api, record)
        self.connect(team, 'Alice')
        service.save_note(None, 'Folder task', 'Uses team-specific delivery receipts',
                           30, time.time() - 60)
        self.sync(team)
        delivered = []
        service.notification.connect(lambda note, kind: delivered.append((note['id'], kind)))
        service.tick()
        self.assertEqual(delivered, [(record['id'], 'reminder')])

    def test_existing_folder_identity_cannot_show_api_owned_customer_popups(self):
        store, service, team, business, api = self.client('alice', connect=True)
        note = service.save_note(None, 'Legacy folder order', 'Legacy folder customer detail',
                                  30, details={'kind': 'order'})
        self.sync(team)
        record = self.api_record(store)
        record['id'] = record['entry']['id'] = note['id']
        self.login(business, api, record)
        service.changed.emit(record['id'])
        delivered = []
        team.notification.connect(delivered.append)
        team.can_notify = lambda: True
        self.sync(team)
        self.assertEqual(delivered, [])
        business.set_session(api.base_url, '', None)
        self.sync(team)
        self.assertEqual(delivered, [])
        self.assertIsNone(store.find(record['id']))

    def test_connecting_personal_notebook_to_team_removes_old_ai_history(self):
        import config
        from main import BuddyApp

        settings = AppSettings(self.root / 'privacy-app' / 'settings.json')
        settings.save({'ai_autonomy': False, 'ai_greetings': False, 'memory_ai': False,
                       'semantic_memory_enabled': False, 'desktop_enabled': False,
                       'desktop_random': False, 'mouse_mode': 'off', 'speech': False,
                       'pet_enabled': False, 'home_on_start': False, 'overlay_visible': False,
                       'note_reminders': False, 'team_share_notes_with_ai': False})
        with patch.object(config, 'team_data_directory', return_value=self.root / 'privacy-profile'), \
                patch('main.CredentialStore.get', return_value=''):
            buddy = BuddyApp(APP, settings, show_tray=False)
        try:
            secret = 'Synthetic workplace detail previously discussed as a personal note'
            note = buddy.note_service.save_note(None, 'Before team connection', secret, 0)
            buddy.chat.history = [{'role': 'assistant', 'content': secret}]
            buddy.chat.pending = [{'role': 'system', 'content': secret}]
            buddy.brain.recent = [{'action': 'animate', 'animation': 'READ', 'say': secret}]
            buddy.voice.cloud_recent = [secret]
            settings.save({'team_sync_enabled': True, 'team_shared_folder': str(self.share),
                           'team_employee_name': 'Synthetic employee'})
            buddy.configure_team()
            self.assertEqual(buddy.chat.history, [])
            self.assertEqual(buddy.chat.pending, [])
            self.assertEqual(buddy.brain.recent, [])
            self.assertEqual(buddy.voice.cloud_recent, [])
            self.wait(lambda: bool(buddy.team.identity()) and not buddy.team._busy)
            self.assertFalse(note_allowed_for_ai(buddy.notes_store.find(note['id']), settings))
            buddy.chat.turn_query = 'Before team connection'
            self.assertNotIn(secret, buddy.chat.system_message()['content'])
        finally:
            buddy.shutdown()
            for window in (buddy.home, buddy.notepad, buddy.chat, buddy.work_chat,
                           buddy.team_window, buddy.dialog, buddy.memory_window,
                           buddy.process_window, buddy.launcher_window, buddy.overlay,
                           buddy.pet, buddy.letters):
                window.hide()
            APP.processEvents()


if __name__ == '__main__':
    unittest.main()
