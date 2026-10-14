"""Two-computer notebook sync, durable queues and actual popup delivery."""
import copy
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from notes import NoteService, NoteStore
from settings import AppSettings
from shared_workspace import SharedWorkspace
from team_service import TeamService, shared_note_payload
from team_store import TeamStore


class TeamServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.share = self.root / 'shared'
        self.share.mkdir()
        self.clients = []

    def tearDown(self):
        for store, notes, team, _ in self.clients:
            team.stop()
        for store, notes, team, _ in self.clients:
            team._thread.join(timeout=2)
            self.assertFalse(team._thread.is_alive(), 'Synthetic work-sync worker did not finish')
            notes.stop()
            store.close()
        QTest.qWait(20)
        self.temp.cleanup()

    def wait(self, predicate, timeout=6):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            QTest.qWait(5)
        self.fail('Timed out waiting for work sync: ' + '; '.join(client[2].status for client in self.clients))

    def client(self, name, folder=None, employee_id=None, notify=False):
        base = folder or self.root / name
        store = NoteStore(base / 'notebook' / 'notes.json')
        settings = AppSettings(base / 'settings.json')
        notes = NoteService(store, settings, lambda: False)
        team = TeamService(notes, settings, base / 'local-profile' / 'team.sqlite', can_notify=lambda: notify)
        errors = []
        team.error.connect(errors.append)
        self.clients.append((store, notes, team, errors))
        team.configure(str(self.share), name, employee_id)
        self.wait(lambda: bool(team.identity()) and not team._busy)
        return store, notes, team, errors

    def sync(self, team):
        self.wait(lambda: not team._busy)
        team.pump()
        self.wait(lambda: not team._busy)
        self.app.processEvents()

    def wire(self):
        return SharedWorkspace(self.share).read_events()[0]

    def test_two_computers_share_content_customer_schedule_and_local_pins_stay_local(self):
        a, an, at, ae = self.client('Alice')
        b, bn, bt, be = self.client('Bob')
        due = time.time() + 3600
        note = an.save_note(None, 'Customer pickup', 'Bring order to reception.', 30, due,
                            details={'kind': 'order', 'customer': 'Customer A', 'order_ref': 'ORDER-1',
                                     'order_due': due, 'team_recipients': [bt.identity()['id']]})
        an.modify(note['id'], pinned=True, pin_position=[30, 50], source='C:/private/list.txt')
        self.sync(at)
        self.sync(bt)
        incoming = b.find(note['id'])
        self.assertIsNotNone(incoming)
        self.assertEqual(incoming['customer'], 'Customer A')
        self.assertEqual(incoming['team_due_at'], due)
        self.assertFalse(incoming['unannounced'])
        self.assertIsNone(incoming['next_due'])
        self.assertFalse(incoming['pinned'])
        self.assertEqual(incoming['source'], '')
        self.assertIsNone(b.next_notification(time.time() + 10000)[0])
        self.assertEqual(a.find(note['id'])['pin_position'], [30, 50])
        for event in self.wire():
            self.assertNotIn('C:/private', str(event))
        self.assertEqual(ae + be, [])

    def test_cache_pin_receipt_and_personal_snooze_changes_do_not_publish_note_versions(self):
        store, notes, team, errors = self.client('Alice')
        note = notes.save_note(None, 'Check order', 'Review pickup time.', 30)
        self.sync(team)
        before = len([e for e in self.wire() if e['kind'] == 'note'])
        notes.modify(note['id'], pinned=True, pin_position=[100, 200], ai_guidance={}, reminder_history=['Already said this'])
        notes.snooze(note['id'], 10)
        self.sync(team)
        self.assertEqual(before, len([e for e in self.wire() if e['kind'] == 'note']))
        self.assertEqual(errors, [])

    def test_explicit_schedule_change_and_completion_are_shared(self):
        a, an, at, _ = self.client('Alice')
        b, bn, bt, _ = self.client('Bob')
        note = an.save_note(None, 'Call customer', 'Ask about delivery.', 30)
        self.sync(at)
        self.sync(bt)
        due = time.time() + 7200
        an.save_note(note['id'], note['title'], note['body'], 60, due)
        self.sync(at)
        self.sync(bt)
        self.assertEqual(b.find(note['id'])['team_due_at'], due)
        self.assertEqual(b.find(note['id'])['repeat_minutes'], 60)
        an.complete(note['id'])
        self.sync(at)
        self.sync(bt)
        self.assertTrue(b.find(note['id'])['done'])

    def test_delete_propagates_and_restart_reconciles_local_commit_without_signal(self):
        a, an, at, _ = self.client('Alice')
        b, bn, bt, _ = self.client('Bob')
        note = an.save_note(None, 'Saved task', 'Original.', 0)
        self.sync(at)
        self.sync(bt)
        # Simulate a notebook fsync followed by a crash before changed.emit.
        a.edit(note['id'], 'Saved task', 'Committed before crash.', 0)
        at.stop()
        at2 = TeamService(an, at.settings, at.profile_path, can_notify=lambda: False)
        self.clients.append((a, an, at2, []))
        at2.configure(str(self.share), 'Alice')
        self.wait(lambda: bool(at2.identity()) and not at2._busy)
        self.sync(bt)
        self.assertEqual(b.find(note['id'])['body'], 'Committed before crash.')
        an.delete(note['id'])
        self.sync(at2)
        self.sync(bt)
        self.assertIsNone(b.find(note['id']))
        # Repeated polls cannot recreate the tombstoned note.
        self.sync(bt)
        self.sync(at2)
        self.assertIsNone(a.find(note['id']))

    def test_offline_edit_is_queued_locally_and_published_when_share_returns(self):
        a, an, at, errors = self.client('Alice')
        workspace = self.share / 'JefferyWorkspace'
        unavailable = self.share / 'offline-workspace'
        workspace.rename(unavailable)
        note = an.save_note(None, 'Offline task', 'Keep this pending.', 0)
        self.sync(at)
        cached = TeamStore(at.profile_path)
        try:
            self.assertTrue(cached.pending_events())
        finally:
            cached.close()
        self.assertIn('unavailable', at.status.lower())
        unavailable.rename(workspace)
        self.sync(at)
        self.assertTrue(any(e['kind'] == 'note' and e['entity_id'] == note['id'] for e in self.wire()))
        self.assertEqual(errors, [])

    def test_full_document_streams_into_independent_local_database_without_truncation(self):
        a, an, at, ae = self.client('Alice')
        b, bn, bt, be = self.client('Bob')
        body = ('Second-page memory 🐈 café\n' * 1500) + 'the very last line'
        note = an.save_note(None, 'Customer reference', body, 0)
        self.sync(at)
        self.sync(bt)
        incoming = b.find(note['id'])
        self.assertIsNotNone(incoming)
        self.assertNotEqual(incoming['document_id'], note['document_id'])
        self.assertEqual(''.join(b.documents.iter_text(incoming['document_id'])), body)
        self.assertEqual(incoming['body_characters'], len(body))
        for event in self.wire():
            self.assertNotIn('document_id', event['payload'])
        # Incoming document metadata must not create an echo revision.
        before = len([e for e in self.wire() if e['kind'] == 'note'])
        self.sync(bt)
        self.sync(at)
        self.assertEqual(before, len([e for e in self.wire() if e['kind'] == 'note']))
        self.assertEqual(ae + be, [])

    def test_restart_offline_restores_identity_inbox_and_durable_message_queue(self):
        a, an, at, _ = self.client('Alice')
        b, bn, bt, _ = self.client('Bob')
        bob = bt.identity()['employee_id']
        at.send_message('Cached customer reminder', 'Remember this after restart.', [bob])
        self.wait(lambda: len(at.entities('message')) == 1 and not at._busy)
        self.sync(bt)
        bt.stop()
        bt._thread.join(timeout=2)
        disconnected = self.root / 'disconnected-share'
        self.share.rename(disconnected)
        returning = TeamService(bn, bt.settings, bt.profile_path, can_notify=lambda: False)
        errors = []
        returning.error.connect(errors.append)
        self.clients.append((b, bn, returning, errors))
        returning.configure(str(self.share), 'Bob')
        self.wait(lambda: bool(returning.identity()) and not returning._busy)
        self.assertEqual(returning.identity()['employee_id'], bob)
        self.assertEqual(returning.inbox()[0]['title'], 'Cached customer reminder')
        self.assertIn('unavailable', returning.status.lower())
        self.assertFalse(self.share.exists())
        returning.send_message('Offline reply', 'Durably saved until the folder returns.', [at.identity()['employee_id']])
        self.wait(lambda: len(returning.entities('message')) == 2 and not returning._busy)
        with_cache = TeamStore(returning.profile_path)
        try:
            self.assertTrue(any(event['kind'] == 'message' for event in with_cache.pending_events()))
        finally:
            with_cache.close()
        disconnected.rename(self.share)
        self.sync(returning)
        self.sync(at)
        self.assertTrue(any(message['title'] == 'Offline reply' for message in at.inbox()))
        self.assertEqual(errors, [])

    def test_unremembered_folder_cannot_reuse_cached_workspace_or_queue_to_previous_team(self):
        store, notes, team, errors = self.client('Alice')
        original_identity = team.identity()
        team.configure(str(self.root / 'unremembered-unavailable-folder'), 'Alice')
        self.wait(lambda: bool(errors) and not team._busy)
        self.assertFalse(team.enabled)
        self.assertFalse(team.send_message('Should not send', 'A different folder was selected.', []))
        cached = TeamStore(team.profile_path)
        try:
            self.assertEqual(cached.identity(), {key: value for key, value in original_identity.items() if key != 'id'})
            self.assertEqual(cached.entities('message'), [])
        finally:
            cached.close()

    def test_opening_unassigned_shared_note_does_not_create_a_receipt_or_error(self):
        a, an, at, errors = self.client('Alice')
        b, bn, bt, _ = self.client('Bob')
        note = an.save_note(None, 'Bob customer reminder', 'Only Bob receives the alert.', 30,
                            details={'team_recipients': [bt.identity()['employee_id']]})
        self.sync(at)
        self.assertFalse(at.mark_note_read(note['id']))
        self.sync(at)
        cache = TeamStore(at.profile_path)
        try:
            self.assertFalse(cache.receipt(at.identity()['employee_id'], 'note', note['id'])['read'])
        finally:
            cache.close()
        self.assertEqual(errors, [])

    def test_api_reserved_note_cannot_materialize_or_generate_a_shared_folder_popup(self):
        store, notes, team, errors = self.client('Alice', notify=True)
        reserved = uuid.uuid4().hex
        team.exclude_note_ids = lambda: {reserved}
        notices = []
        team.notification.connect(notices.append)
        transport = SharedWorkspace(self.share)
        producer = TeamStore(self.root / 'synthetic-producer.sqlite')
        try:
            producer.configure(transport.workspace_id, 'Synthetic sender')
            event = producer.put_note({'id': reserved, 'title': 'API-owned customer order',
                                       'body': 'The API already owns its alert.', 'created': time.time(),
                                       'repeat_minutes': 30, 'recipients': [team.identity()['employee_id']]})
            transport.publish_event(event)
        finally:
            producer.close()
        self.sync(team)
        self.assertIsNone(store.find(reserved))
        self.assertEqual(notices, [])
        self.assertFalse(team.notification_active({'entity_id': reserved, 'entity_kind': 'note'}))
        cached = TeamStore(team.profile_path)
        try:
            # The event is retained for integrity; only API-owned UI delivery is suppressed.
            self.assertTrue(cached.due_notifications(team.identity()['employee_id'], time.time()))
        finally:
            cached.close()
        self.assertEqual(errors, [])

    def test_notification_receipt_is_saved_only_after_actual_popup_and_reminder_is_targeted(self):
        a, an, at, _ = self.client('Alice')
        b, bn, bt, _ = self.client('Bob', notify=True)
        c, cn, ct, _ = self.client('Carol', notify=True)
        notifications, other_notifications = [], []
        bt.notification.connect(notifications.append)
        ct.notification.connect(other_notifications.append)
        at.send_message('Call Customer A', 'Confirm tomorrow’s collection.', [bt.identity()['id']], repeat_minutes=1)
        self.wait(lambda: not at._busy and len(at.entities('message')) == 1)
        self.sync(bt)
        self.sync(ct)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(other_notifications, [])
        notice = notifications[0]
        cache = TeamStore(bt.profile_path)
        try:
            receipt = cache.receipt(bt.identity()['id'], 'message', notice['id'])
            self.assertEqual(receipt['presented'], set())
        finally:
            cache.close()
        self.assertTrue(bt.mark_notification_presented(notice))
        self.wait(lambda: bt._pending is None and not bt._busy)
        self.sync(bt)
        cache = TeamStore(bt.profile_path)
        try:
            receipt = cache.receipt(bt.identity()['id'], 'message', notice['id'])
            self.assertIn(notice['occurrence_id'], receipt['presented'])
            self.assertFalse(receipt['acknowledged'])
            next_notices = cache.due_notifications(bt.identity()['id'], time.time() + 120)
            self.assertEqual(next_notices[0]['notification_kind'], 'reminder')
        finally:
            cache.close()

    def test_quiet_mode_does_not_consume_popup_receipts(self):
        a, an, at, _ = self.client('Alice')
        b, bn, bt, _ = self.client('Bob', notify=True)
        bt.settings.save({'quiet_mode': True})
        delivered = []
        bt.notification.connect(delivered.append)
        at.send_message('Heads up', 'A delivery is coming.', [bt.identity()['id']])
        self.wait(lambda: len(at.entities('message')) == 1 and not at._busy)
        self.sync(bt)
        self.assertEqual(delivered, [])
        bt.settings.save({'quiet_mode': False})
        self.sync(bt)
        self.assertEqual(len(delivered), 1)

    def test_concurrent_offline_edits_preserve_both_versions_and_can_be_resolved(self):
        a, an, at, ae = self.client('Alice')
        b, bn, bt, be = self.client('Bob')
        note = an.save_note(None, 'Delivery instructions', 'Original.', 0)
        self.sync(at)
        self.sync(bt)
        # Both notebooks commit from the same causal base before either polls.
        a.edit(note['id'], note['title'], 'Alice’s change.', 0)
        b.edit(note['id'], note['title'], 'Bob’s change.', 0)
        self.sync(at)
        self.sync(bt)
        self.sync(at)
        bodies = {n['body'] for n in a.notes if n.get('team_id') == note['id']}
        self.assertEqual(bodies, {'Alice’s change.', 'Bob’s change.'})
        self.assertIn('Conflict', at.note_sync_status(note['id']))
        selected = next(n for n in a.notes if n['body'] == 'Alice’s change.')
        at.resolve_note_conflict(selected['id'])
        self.wait(lambda: not at._busy)
        self.sync(at)
        self.sync(bt)
        self.assertEqual([n['body'] for n in b.notes if n.get('team_id') == note['id']], ['Alice’s change.'])
        self.assertEqual(ae + be, [])

    def test_invalid_connection_reports_error_without_freezing_retries(self):
        store, notes, team, errors = self.client('Alice')
        team.configure(str(self.root / 'does-not-exist'), 'Alice')
        self.wait(lambda: bool(errors) and not team._busy)
        team.configure(str(self.share), 'Alice')
        self.wait(lambda: not team._busy and 'Connected' in team.status)

    def test_idle_sync_does_not_rewrite_notebook(self):
        store, notes, team, _ = self.client('Alice')
        notes.save_note(None, 'Saved note', 'Nothing changed.', 0)
        self.sync(team)
        with patch.object(store, 'save', wraps=store.save) as save:
            self.sync(team)
        self.assertEqual(save.call_count, 0)

    def test_work_note_is_private_before_background_sync_can_run(self):
        store, notes, team, _ = self.client('Alice')
        observed = []
        notes.changed.connect(lambda identifier: observed.append(copy.deepcopy(store.find(identifier))))
        note = notes.save_note(None, 'Private customer', 'Confidential pickup details.', 0)
        self.assertTrue(note['team_shared'])
        self.assertTrue(observed[-1]['team_shared'])
        portable = store.export_json()['notes'][0]
        self.assertTrue(portable['team_shared'])
        self.assertEqual([key for key in portable if key.startswith('team_')], ['team_shared'])

    def test_cached_notification_still_appears_and_receipt_is_queued_offline(self):
        a, an, at, _ = self.client('Alice')
        b, bn, bt, errors = self.client('Bob')
        at.send_message('Customer reminder', 'Call Customer A.', [bt.identity()['id']], repeat_minutes=1)
        self.wait(lambda: len(at.entities('message')) == 1 and not at._busy)
        self.sync(bt)
        notices = []
        bt.notification.connect(notices.append)
        bt.can_notify = lambda: True
        workspace = self.share / 'JefferyWorkspace'
        offline = self.share / 'offline-workspace'
        workspace.rename(offline)
        self.sync(bt)
        self.assertEqual(len(notices), 1)
        self.assertTrue(notices[0]['_offline'])
        self.assertIsNone(notices[0]['_claim_token'])
        self.assertTrue(bt.mark_notification_presented(notices[0]))
        self.wait(lambda: not bt._busy)
        self.sync(bt)
        cache = TeamStore(bt.profile_path)
        try:
            self.assertIn(notices[0]['occurrence_id'], cache.receipt(bt.identity()['id'], 'message', notices[0]['id'])['presented'])
            self.assertTrue(any(event['kind'] == 'receipt' for event in cache.pending_events()))
        finally:
            cache.close()
        offline.rename(workspace)
        self.assertEqual(errors, [])

    def test_network_profile_path_is_rejected_before_sqlite_is_created(self):
        store, notes, team, _ = self.client('Alice')
        destination = self.root / 'network-profile' / 'team.sqlite'
        with patch('config._network_location', return_value=True):
            with self.assertRaisesRegex(ValueError, 'on this computer'):
                TeamService(notes, team.settings, destination)
        self.assertFalse(destination.exists())


if __name__ == '__main__':
    unittest.main()
