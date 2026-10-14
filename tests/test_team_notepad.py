"""Shared notebook assignment, receipts, and safe incoming editor updates."""
from pathlib import Path
import json
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import zipfile

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from notes import NoteStore, NoteService
from notepad_window import NotepadWindow, reminder_time
from settings import AppSettings


app = QApplication.instance() or QApplication([])


class Team(QObject):
    changed = Signal()
    status_changed = Signal(str)

    def __init__(self):
        super().__init__()
        self.enabled = True
        self.status = 'Connecting to the shared work folder…'
        self.members = [{'id': 'alice', 'name': 'Alice'}, {'id': 'bob', 'name': 'Bob'}]
        self.read = []
        self.conflicts = set()
        self.resolved = []

    def roster(self):
        return self.members

    def mark_note_read(self, identifier):
        self.read.append(identifier)

    def note_sync_status(self, identifier):
        if identifier in self.conflicts:
            return 'Conflict: both versions preserved; choose a version to resolve'
        return 'Synced'

    def note_receipt_status(self, identifier):
        return 'Read by 1 employee'

    def resolve_note_conflict(self, identifier):
        self.resolved.append(identifier)
        return True


class TeamNotepadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.root = root
        self.settings = AppSettings(root / 'settings.json')
        self.store = NoteStore(root / 'notes.json')
        self.service = NoteService(self.store, self.settings, lambda: False)
        self.window = NotepadWindow(self.service, self.settings)
        self.team = Team()

    def tearDown(self):
        self.window.shutdown()
        self.window.close()
        self.temp.cleanup()

    def select(self, identifier):
        for index in range(self.window.team_members.count()):
            item = self.window.team_members.item(index)
            if item.data(Qt.UserRole) == identifier:
                item.setCheckState(Qt.Checked)
                return
        self.fail('The employee was not listed.')

    def test_work_team_shortcut_and_optional_controls(self):
        opened = []
        self.window.team_requested.connect(lambda: opened.append(True))
        self.window.team_button.click()
        self.assertEqual(opened, [True])
        self.assertTrue(self.window.team_controls.isHidden())
        self.window.set_team_service(self.team)
        self.assertFalse(self.window.team_controls.isHidden())
        self.assertEqual(self.window.team_members.count(), 2)
        self.team.enabled = False
        self.team.changed.emit()
        self.assertTrue(self.window.team_controls.isHidden())

    def test_sidebar_status_and_settings_follow_active_shared_folder(self):
        folder_opened, server_opened = [], []
        self.window.team_requested.connect(lambda: folder_opened.append(True))
        self.window.connection_work_chat_requested.connect(lambda: server_opened.append(True))
        self.window.set_team_service(self.team)
        self.assertEqual(self.window.shared_status.text(), 'Shared folder · Connecting')
        self.assertEqual(self.window.shared_login_button.text(), 'Folder settings')
        self.window.shared_login_button.click()
        self.assertEqual(folder_opened, [True])
        self.assertEqual(server_opened, [])
        self.team.status = 'Connected. Notes and employee reminders are synced.'
        self.team.status_changed.emit(self.team.status)
        self.assertEqual(self.window.shared_status.text(), 'Shared folder · Synced')
        self.window.set_shared_status('Sign in to Work Chat to share business records.', False)
        self.assertEqual(self.window.shared_status.text(), 'Shared folder · Synced')
        self.team.status = 'Offline. Saved changes will sync when the shared folder returns.'
        self.team.status_changed.emit(self.team.status)
        self.assertEqual(self.window.shared_status.text(), 'Shared folder · Offline')
        self.assertEqual(self.window.shared_status.toolTip(), self.team.status)
        self.window.set_shared_status('Server connected.', True)
        self.team.enabled = False
        self.team.changed.emit()
        self.assertEqual(self.window.shared_status.text(), 'Connected to your team')
        self.assertEqual(self.window.shared_login_button.text(), 'Team chat')
        self.window.shared_login_button.click()
        self.assertEqual(server_opened, [True])
        self.assertEqual(folder_opened, [True])

    def test_sidebar_without_folder_keeps_existing_server_sign_in(self):
        self.window.set_shared_status('Sign in to Work Chat to share business records.', False)
        self.assertEqual(self.window.shared_status.text(), 'Sign in to share orders and checklists.')
        self.assertEqual(self.window.shared_login_button.text(), 'Team sign in')
        self.window.set_shared_status('Loading Famous Twins orders…', False)
        self.assertEqual(self.window.shared_status.text(), 'Connecting to your team...')
        self.window.set_shared_status('Office server unavailable.', False)
        self.assertEqual(self.window.shared_status.text(), 'Team server unavailable')

    def test_assignment_saved_and_everyone_resets_it(self):
        self.window.set_team_service(self.team)
        self.window.title.setText('Check the delivery')
        self.window.body.setPlainText('Call the customer before pickup.')
        self.window.team_everyone.setChecked(False)
        self.select('bob')
        self.assertTrue(self.window.save_note())
        note = self.store.find(self.window.editing_id)
        self.assertEqual(note['team_recipients'], ['bob'])
        self.window.team_everyone.setChecked(True)
        self.assertTrue(self.window.save_note())
        self.assertEqual(note['team_recipients'], [])

    def test_selected_mode_requires_an_employee(self):
        self.window.set_team_service(self.team)
        self.window.title.setText('Customer callback')
        self.window.team_everyone.setChecked(False)
        with patch('notepad_window.QMessageBox.warning') as warning:
            self.assertFalse(self.window.save_note())
        self.assertIn('Choose at least one employee', warning.call_args.args[2])
        self.assertEqual(self.store.notes, [])

    def test_roster_refresh_preserves_draft_and_unknown_assignment(self):
        note = self.service.save_note(None, 'Old title', 'Saved body', 0,
                                      details={'team_recipients': ['alice', 'former-employee']})
        self.window.set_team_service(self.team)
        self.window.load_note(note['id'])
        self.window.body.setPlainText('Do not overwrite this draft.')
        self.select('bob')
        self.team.members = [{'id': 'bob', 'name': 'Robert'}]
        self.team.changed.emit()
        self.assertEqual(set(self.window.selected_team_recipients()), {'alice', 'bob', 'former-employee'})
        self.assertEqual(self.window.body.toPlainText(), 'Do not overwrite this draft.')
        self.assertTrue(self.window.dirty)

    def test_opening_shared_note_marks_read_without_completing(self):
        note = self.service.save_note(None, 'Read the pickup details', 'Bring the order to reception.', 0)
        self.window.set_team_service(self.team)
        self.window.load_note(note['id'])
        self.assertEqual(self.team.read, [note['id']])
        self.assertFalse(note['done'])
        self.assertIn('Synced', self.window.team_note_status.text())
        self.assertIn('Read by 1 employee', self.window.team_note_status.text())

    def test_remote_edits_refresh_clean_editor_and_preserve_dirty_editor(self):
        note = self.service.save_note(None, 'Original', 'Original body', 0)
        self.window.load_note(note['id'])
        self.service.save_note(note['id'], 'Updated by colleague', 'Changed on another PC.', 0)
        self.assertEqual(self.window.title.text(), 'Updated by colleague')
        self.assertEqual(self.window.body.toPlainText(), 'Changed on another PC.')
        self.assertFalse(self.window.dirty)
        self.window.body.setPlainText('Local unsaved draft')
        self.service.save_note(note['id'], 'Another update', 'New saved body', 0)
        self.assertEqual(self.window.body.toPlainText(), 'Local unsaved draft')
        self.assertTrue(self.window.dirty)

    def test_shared_due_editor_uses_shared_schedule_instead_of_personal_snooze(self):
        note = self.service.save_note(None, 'Order deadline', 'Prepare the delivery.', 0)
        due = int(time.time() + 7200)
        note.update(team_id='a' * 32, team_due_at=due, next_due=None)
        self.window.set_team_service(self.team)
        self.window.load_note(note['id'])
        self.assertTrue(self.window.scheduled.isChecked())
        self.assertEqual(self.window.due.dateTime().toSecsSinceEpoch(), due)
        self.assertNotEqual(reminder_time(note), 'Timed reminders off')
        self.service.changed.emit(note['id'])
        self.assertEqual(self.window.due.dateTime().toSecsSinceEpoch(), due)

    def test_folder_order_stays_local_with_server_controller_attached(self):
        self.window.shared_business = Mock(_cache_ids=set())
        self.window.set_team_service(self.team)
        self.window.new_entry('order')
        self.window.title.setText('Folder customer order')
        self.window.order_notes.setPlainText('Confirm the customer before pickup.')
        self.window.vehicle.setText('Toyota Corolla')
        self.window.registration.setText('1234 AB')
        self.window.vin.setText('MANUAL-VIN')
        self.window.payment_received.setText('1250.50')
        self.window.part_text.setText('Brake pads')
        self.window.part_number.setText('BP-42')
        self.window.part_supplier.setText('Parts Supplier')
        self.window.part_price.setText('2300.25')
        self.window.add_order_item()
        self.window.team_everyone.setChecked(False)
        self.select('bob')
        self.assertTrue(self.window.save_note())
        note = self.store.find(self.window.editing_id)
        self.assertEqual(note['vehicle'], 'Toyota Corolla')
        self.assertEqual(note['registration'], '1234 AB')
        self.assertEqual(note['vin'], 'MANUAL-VIN')
        self.assertEqual(note['payment_received_cents'], 125050)
        self.assertEqual(note['checklist'][0]['part_number'], 'BP-42')
        self.assertEqual(note['checklist'][0]['supplier'], 'Parts Supplier')
        self.assertEqual(note['checklist'][0]['unit_price_cents'], 230025)
        self.assertEqual(note['team_recipients'], ['bob'])
        self.window.shared_business.save.assert_not_called()
        self.window.toggle_done()
        self.assertTrue(note['done'])
        self.window.shared_business.complete.assert_not_called()
        with patch('notepad_window.QMessageBox.question', return_value=QMessageBox.Yes):
            self.window.delete_note()
        self.assertIsNone(self.store.find(note['id']))
        self.window.shared_business.delete.assert_not_called()

    def test_api_cached_order_keeps_server_ownership_while_folder_sync_enabled(self):
        note = self.service.save_note(None, 'Server order', 'Pickup at the counter.', 0,
                                      details={'kind': 'order'})
        controller = Mock(_cache_ids={note['id']})
        controller.save.return_value = False
        self.window.shared_business = controller
        self.window.set_team_service(self.team)
        self.window.load_note(note['id'])
        self.assertTrue(self.window.uses_business_server(note))
        self.assertTrue(self.window.team_controls.isHidden())
        self.assertEqual(self.team.read, [], 'An API note received a folder-workspace receipt.')
        self.window.team_everyone.setChecked(False)
        for index in range(self.window.team_members.count()):
            self.window.team_members.item(index).setCheckState(Qt.Unchecked)
        self.window.order_notes.setPlainText('A server-owned draft.')
        with patch.object(self.service, 'save_note') as local_save:
            self.assertFalse(self.window.save_note())
            local_save.assert_not_called()
        controller.save.assert_called_once()
        self.assertTrue(self.window.dirty)
        self.assertEqual(note['body'], 'Pickup at the counter.')

    def test_folder_scheduler_delivers_only_api_cached_orders_while_team_active(self):
        pending = self.service.save_note(None, 'Pending folder note', 'Wait for folder sync.', 30)
        api = self.service.save_note(None, 'API pickup', 'An order owned by the office API.', 30,
                                     details={'kind': 'order'})
        shared = self.service.save_note(None, 'Synced folder order', 'Employee receipts own this reminder.', 30,
            details={'kind': 'order', 'team_id': 'shared-order', 'team_workspace_id': 'office'})
        self.service.team_notifications_active = True
        self.service.team_excluded_note_ids = lambda: {api['id']}
        self.service.can_notify = lambda: True
        delivered = []
        self.service.notification.connect(lambda note, kind: delivered.append((note['id'], kind)))
        self.service.tick()
        self.assertEqual(delivered, [(api['id'], 'added')])
        self.assertTrue(pending['unannounced'])
        self.assertTrue(shared['unannounced'])
        self.service.last_delivery = -float('inf')
        self.service.tick()
        self.assertEqual(len(delivered), 1, 'A pending folder note escaped to the local scheduler.')
        self.service.team_notifications_active = False
        self.service.last_delivery = -float('inf')
        self.service.tick()
        self.assertEqual(delivered[-1], (pending['id'], 'added'))
        self.service.last_delivery = -float('inf')
        self.service.tick()
        self.assertEqual(len(delivered), 2, 'A synced folder note escaped after disconnect.')

    def test_folder_ownership_survives_disconnect_and_server_default_is_preserved(self):
        note = self.service.save_note(None, 'Folder order', 'Local cached copy.', 0,
            details={'kind': 'order', 'team_id': 'folder-order', 'team_workspace_id': 'office'})
        self.window.shared_business = Mock(_cache_ids=set())
        self.window.load_note(note['id'])
        self.assertFalse(self.window.uses_business_server(note))
        normal = self.service.save_note(None, 'Server order', 'Normal office API workflow.', 0,
                                       details={'kind': 'order'})
        self.window.load_note(normal['id'])
        self.assertTrue(self.window.uses_business_server(normal))

    def test_schedule_agenda_includes_shared_schedule_and_customer_deadline(self):
        due = time.time() + 3600
        shared = self.service.save_note(None, 'Shared follow-up', 'Customer callback.', 0,
            details={'team_id': 'follow-up', 'team_workspace_id': 'office', 'team_due_at': due})
        order = self.service.save_note(None, 'Customer pickup', 'Prepare for pickup.', 0,
            details={'kind': 'order', 'team_id': 'customer-order', 'team_workspace_id': 'office', 'order_due': due + 60})
        self.window.refresh_dashboard()
        identifiers = [self.window.agenda.item(index).data(Qt.UserRole)
                       for index in range(self.window.agenda.count())]
        self.assertEqual(identifiers, [shared['id'], order['id']])
        self.service.complete(order['id'])
        self.assertEqual(self.window.agenda.count(), 1)

    def test_order_deadline_display_does_not_claim_a_personal_reminder_is_enabled(self):
        note = self.service.save_note(None, 'Pickup deadline', 'Prepare the order.', 0,
                                      details={'kind': 'order', 'order_due': time.time() + 7200})
        self.assertEqual(reminder_time(note), 'Timed reminders off')
        note.update(team_id='shared-order', team_due_at=None)
        self.assertNotEqual(reminder_time(note), 'Timed reminders off')

    def test_remote_deletion_clears_clean_editor_but_keeps_unsaved_draft(self):
        note = self.service.save_note(None, 'Old pickup', 'Saved instructions.', 0)
        self.window.load_note(note['id'])
        self.service.delete(note['id'])
        self.assertIsNone(self.window.editing_id)
        self.assertEqual(self.window.body.toPlainText(), '')
        draft = self.service.save_note(None, 'Keep draft', 'Saved body.', 0)
        self.window.load_note(draft['id'])
        self.window.body.setPlainText('Unsaved customer instructions.')
        self.service.delete(draft['id'])
        self.assertEqual(self.window.body.toPlainText(), 'Unsaved customer instructions.')
        self.assertTrue(self.window.dirty)

    def test_assignment_edit_keeps_full_document_and_current_page(self):
        text = 'Customer instructions ' * 1000 + '\nThe final pickup detail.'
        note = self.service.save_note(None, 'Long order instructions', text, 0)
        self.window.set_team_service(self.team)
        self.window.load_note(note['id'])
        last_page = self.window.document_page.maximum()
        self.window.document_page.setValue(last_page)
        self.assertIn('final pickup detail', self.window.body.toPlainText())
        self.window.team_everyone.setChecked(False)
        self.select('alice')
        self.assertTrue(self.window.save_note())
        self.assertTrue(self.window.body.isReadOnly())
        self.assertEqual(self.window.document_page.value(), last_page)
        self.assertEqual(''.join(self.store.documents.iter_text(note['document_id'])), text)
        self.assertEqual(note['team_recipients'], ['alice'])

    def test_conflict_resolution_requires_confirming_saved_version(self):
        note = self.service.save_note(None, 'Choose this version', 'Customer pickup at reception.', 0)
        self.team.conflicts.add(note['id'])
        self.window.set_team_service(self.team)
        self.window.load_note(note['id'])
        self.assertFalse(self.window.team_conflict_button.isHidden())
        self.assertTrue(self.window.team_conflict_button.isEnabled())
        with patch('notepad_window.QMessageBox.question', return_value=QMessageBox.No):
            self.assertFalse(self.window.resolve_team_note_conflict())
        self.assertEqual(self.team.resolved, [])
        with patch('notepad_window.QMessageBox.question', return_value=QMessageBox.Yes) as question:
            self.assertTrue(self.window.resolve_team_note_conflict())
        self.assertEqual(question.call_args.args[1], 'Use this saved version?')
        self.assertEqual(self.team.resolved, [note['id']])
        self.assertEqual(note['body'], 'Customer pickup at reception.')

    def test_conflict_resolution_preserves_unsaved_draft(self):
        note = self.service.save_note(None, 'Review conflicting version', 'Saved body.', 0)
        self.team.conflicts.add(note['id'])
        self.window.set_team_service(self.team)
        self.window.load_note(note['id'])
        self.window.body.setPlainText('Unsaved changes that must survive.')
        self.assertFalse(self.window.team_conflict_button.isEnabled())
        with patch('notepad_window.QMessageBox.question') as question:
            self.assertFalse(self.window.resolve_team_note_conflict())
            question.assert_not_called()
        self.assertEqual(self.team.resolved, [])
        self.assertEqual(self.window.body.toPlainText(), 'Unsaved changes that must survive.')
        self.assertTrue(self.window.dirty)

    def finish_export(self):
        deadline = time.monotonic() + 8
        while self.window.transfer.busy and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        app.processEvents()
        self.assertFalse(self.window.transfer.busy, 'Notebook export did not finish.')

    def test_mobile_json_export_strips_employee_transport_and_keeps_shared_marker(self):
        note = self.service.save_note(None, 'Shared customer details', 'Shared note content.', 0,
            details={'team_id': 'shared-order', 'team_workspace_id': 'office',
                     'team_revisions': ['revision-1'], 'team_recipients': ['alice'],
                     'team_due_at': time.time() + 3600})
        note['team_receipts'] = {'alice': {'read': True}}
        self.service.save_note(None, 'Queued assignment', 'Pending sync.', 0,
                               details={'team_recipients': ['bob']})
        self.service.save_note(None, 'Personal note', 'Personal content.', 0)
        destination = self.root / 'mobile.json'
        self.assertTrue(self.window.export_backup(str(destination)))
        self.finish_export()
        payload = json.loads(destination.read_text(encoding='utf-8'))
        exported, queued, personal = payload['notes']
        self.assertEqual([key for key in exported if key.startswith('team_')], ['team_shared'])
        self.assertIs(exported['team_shared'], True)
        self.assertIs(queued['team_shared'], True)
        self.assertNotIn('team_shared', personal)
        self.assertEqual(exported['body'], 'Shared note content.')
        self.assertEqual(note['team_id'], 'shared-order', 'Export changed the active notebook.')

    def test_full_zip_backup_retains_shared_note_links(self):
        self.service.save_note(None, 'Shared reminder', 'Keep its restore link.', 0,
            details={'team_id': 'shared-reminder', 'team_workspace_id': 'office',
                     'team_revisions': ['revision-1'], 'team_recipients': ['alice']})
        destination = self.root / 'desktop.zip'
        self.assertTrue(self.window.export_backup(str(destination)))
        self.finish_export()
        with zipfile.ZipFile(destination) as archive:
            note = json.loads(archive.read('notebook.json'))['notes'][0]
        self.assertEqual(note['team_id'], 'shared-reminder')
        self.assertEqual(note['team_workspace_id'], 'office')
        self.assertEqual(note['team_revisions'], ['revision-1'])
        self.assertEqual(note['team_recipients'], ['alice'])


if __name__ == '__main__':
    unittest.main()
