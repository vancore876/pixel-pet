"""Two desktop PCs sharing work notes and recipient-specific popup reminders.

Run with QT_QPA_PLATFORM=offscreen. This uses real BuddyApp windows, independent
local profiles and a temporary shared folder; no Groq key or work share is used.
Only the work modules' clocks are advanced, so Qt and worker shutdown use real
time. Set BUDDY_QA_DIR to retain screenshots of the message and customer flow.
"""
from contextlib import ExitStack
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QDateTime, Qt, qInstallMessageHandler
from PySide6.QtWidgets import QApplication

import config
import shared_workspace
import team_service
import team_store
from main import BuddyApp
from settings import AppSettings


qInstallMessageHandler(lambda kind, context, text: print(text, file=sys.stderr)
    if 'This plugin does not support' not in text else None)


def run():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    buddies, errors = [], []
    clock = SimpleNamespace(value=time.time())
    work_time = SimpleNamespace(time=lambda: clock.value, monotonic=time.monotonic)
    preview = os.environ.get('BUDDY_QA_DIR')
    if preview:
        Path(preview).mkdir(parents=True, exist_ok=True)

    def screenshot(widget, filename):
        if preview:
            assert widget.grab().save(str(Path(preview) / filename))

    def wait_for(predicate, label, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for buddy in buddies:
                if not buddy.shutting_down:
                    buddy.team.timer.stop()
                    if not buddy.team._busy:
                        buddy.team.pump()
            app.processEvents()
            if errors:
                raise AssertionError('; '.join(errors))
            if predicate():
                return
            time.sleep(0.015)
        raise AssertionError('Timed out: ' + label)

    def assert_no_popup(buddy, label, duration=0.25):
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            for current in buddies:
                if not current.shutting_down and not current.team._busy:
                    current.team.pump()
            app.processEvents()
            assert not buddy.team_popup.isVisible(), label
            if errors:
                raise AssertionError('; '.join(errors))
            time.sleep(0.015)

    def inbox_row(buddy, identifier):
        return next((row for row in buddy.team.inbox() if row['id'] == identifier), {})

    def note_by_team_id(buddy, identifier):
        return next((note for note in buddy.notes_store.notes if note.get('team_id') == identifier), None)

    def choose_recipient(widget, identifier):
        for index in range(widget.count()):
            item = widget.item(index)
            item.setCheckState(Qt.Checked if item.data(Qt.UserRole) == identifier else Qt.Unchecked)
        assert any(widget.item(i).data(Qt.UserRole) == identifier for i in range(widget.count()))

    with tempfile.TemporaryDirectory(prefix='buddy-team-gui-') as folder, ExitStack() as patches:
        root = Path(folder)
        shared = root / 'shared'
        shared.mkdir()
        # Replacing each module's time reference leaves Python's global clock,
        # QTimer, network timeouts and our bounded waits untouched.
        for module in (team_service, team_store, shared_workspace):
            patches.enter_context(patch.object(module, 'time', work_time))
        patches.enter_context(patch('main.CredentialStore.get', return_value=''))

        def create_buddy(label, profile=None, copied=None):
            local = root / label
            local.mkdir(exist_ok=True)
            settings = AppSettings(local / 'settings.json')
            settings.save(copied or {'ai_autonomy': False, 'ai_greetings': False,
                'memory_ai': False, 'desktop_enabled': False, 'desktop_random': False,
                'mouse_mode': 'off', 'speech': False, 'pet_enabled': False,
                'semantic_memory_enabled': False, 'team_sync_enabled': False})
            with patch.object(config, 'team_data_directory', return_value=profile or local / 'profile'):
                buddy = BuddyApp(app, settings, show_tray=False)
            buddies.append(buddy)
            buddy.team.error.connect(lambda text, name=label: errors.append(name + ': ' + text))
            buddy.team.timer.stop()
            return buddy

        def connect(buddy, name):
            window = buddy.team_window
            window.shared_folder.setText(str(shared))
            window.employee_choice.setCurrentIndex(0)
            window.employee_name.setText(name)
            window.connect_button.click()
            wait_for(lambda: bool(buddy.team.identity()), name + ' work connection')
            return buddy.team.identity()['employee_id']

        def send(buddy, recipient, title, repeat=1):
            window = buddy.team_window
            window.refresh()
            window.clear_source()
            window.everyone.setChecked(recipient is None)
            if recipient is not None:
                choose_recipient(window.recipients, recipient)
            window.title.setText(title)
            window.body.setPlainText('Please check the customer pickup desk. <literal text>')
            window.repeat.setValue(repeat)
            window.scheduled.setChecked(False)
            previous = {row['id'] for row in buddy.team.entities('message')}
            window.send_button.click()
            wait_for(lambda: any(row['id'] not in previous for row in buddy.team.entities('message')),
                     'message committed through the composer')
            return next(row['id'] for row in buddy.team.entities('message') if row['id'] not in previous)

        try:
            alice, bob = create_buddy('alice'), create_buddy('bob')
            alice_id, bob_id = connect(alice, 'Alice'), connect(bob, 'Bob')
            assert alice_id != bob_id
            assert alice.team.profile_path != bob.team.profile_path
            wait_for(lambda: len(alice.team.roster()) == len(bob.team.roster()) == 2,
                     'employee roster on both PCs')
            screenshot(alice.team_window, 'team-setup.png')

            message_id = send(alice, bob_id, 'Check the pickup desk')
            wait_for(lambda: bob.team_popup.isVisible() and bob.team_popup.identifier == message_id,
                     'new employee message popup')
            assert bob.team_popup.heading.text() == 'New team message'
            assert '<literal text>' in bob.team_popup.body.toPlainText()
            assert not alice.team_popup.isVisible(), 'A message reached an unassigned employee'
            screenshot(bob.team_popup, 'team-message.png')
            wait_for(lambda: inbox_row(bob, message_id).get('next_due') == clock.value + 60,
                     'visible popup receipt')
            bob.team_popup.dismiss_button.click()
            assert not inbox_row(bob, message_id)['acknowledged']
            bob.show_team()
            bob.team_window.open_message(message_id, entity_kind='message')
            wait_for(lambda: inbox_row(bob, message_id).get('read'), 'opening marks the message read')
            assert not inbox_row(bob, message_id)['acknowledged'], 'Reading acknowledged the message'
            assert_no_popup(bob, 'A dismissed message repeated before its due time')

            clock.value += 61
            wait_for(lambda: bob.team_popup.isVisible(), 'follow-up message reminder')
            assert bob.team_popup.identifier == message_id
            assert bob.team_popup.heading.text() == 'Team message reminder'
            screenshot(bob.team_popup, 'team-message-reminder.png')
            bob.team_popup.snooze_minutes.setCurrentIndex(bob.team_popup.snooze_minutes.findData(15))
            bob.team_popup.snooze_button.click()
            wait_for(lambda: inbox_row(bob, message_id).get('next_due') == clock.value + 900,
                     'recipient snooze receipt')
            clock.value += 899
            assert_no_popup(bob, 'A snoozed message appeared early')
            clock.value += 2
            wait_for(lambda: bob.team_popup.isVisible(), 'reminder after snooze')
            bob.team_popup.acknowledge_button.click()
            wait_for(lambda: inbox_row(bob, message_id).get('acknowledged'), 'recipient acknowledgement')
            clock.value += 3600
            assert_no_popup(bob, 'An acknowledged message still reminded its recipient')

            broadcast_id = send(alice, None, 'Everyone checks their own reminder')
            wait_for(lambda: alice.team_popup.isVisible() and bob.team_popup.isVisible(),
                     'Everyone receives the same message independently')
            assert alice.team_popup.identifier == bob.team_popup.identifier == broadcast_id
            wait_for(lambda: inbox_row(alice, broadcast_id).get('next_due') == clock.value + 60,
                     'sender receives the Everyone message')
            alice.team_popup.dismiss_button.click()
            bob.team_popup.acknowledge_button.click()
            wait_for(lambda: inbox_row(bob, broadcast_id).get('acknowledged'),
                     'Bob acknowledges only his broadcast receipt')
            assert not inbox_row(alice, broadcast_id)['acknowledged']
            clock.value += 61
            wait_for(lambda: alice.team_popup.isVisible(), 'Alice still receives her broadcast reminder')
            assert alice.team_popup.identifier == broadcast_id
            assert_no_popup(bob, 'Another employee acknowledgement was ignored')
            alice.team_popup.acknowledge_button.click()
            wait_for(lambda: inbox_row(alice, broadcast_id).get('acknowledged'),
                     'Alice acknowledges her own broadcast receipt')

            bob.apply_settings({'quiet_mode': True})
            quiet_id = send(alice, bob_id, 'Wait while Bob is quiet')
            wait_for(lambda: bool(inbox_row(bob, quiet_id)), 'quiet-mode inbox sync')
            assert_no_popup(bob, 'Quiet mode displayed a team popup')
            bob.apply_settings({'quiet_mode': False})
            wait_for(lambda: bob.team_popup.isVisible(), 'delivery after quiet mode')
            assert bob.team_popup.identifier == quiet_id
            assert bob.team_popup.heading.text() == 'New team message', 'Quiet mode consumed initial delivery'
            bob.team_popup.acknowledge_button.click()
            wait_for(lambda: inbox_row(bob, quiet_id).get('acknowledged'), 'quiet message acknowledged')

            bob.note_popup.show()
            busy_id = send(alice, bob_id, 'Wait for the personal popup')
            wait_for(lambda: bool(inbox_row(bob, busy_id)), 'busy popup inbox sync')
            assert_no_popup(bob, 'A team card overlapped a personal reminder')
            bob.note_popup.hide()
            wait_for(lambda: bob.team_popup.isVisible(), 'delivery after the existing popup closes')
            assert bob.team_popup.identifier == busy_id
            assert bob.team_popup.heading.text() == 'New team message', 'Busy popup consumed initial delivery'
            bob.team_popup.acknowledge_button.click()
            wait_for(lambda: inbox_row(bob, busy_id).get('acknowledged'), 'busy message acknowledged')

            # A GUI refusal after a worker claim must release that occurrence.
            rejected = []
            with patch.object(bob.team_popup, 'present', side_effect=lambda *args: rejected.append(args) or False):
                rejected_id = send(alice, bob_id, 'Try the card again')
                wait_for(lambda: bool(rejected), 'GUI refuses a claimed notification')
                assert not bob.team_popup.isVisible()
            wait_for(lambda: bob.team_popup.isVisible(), 'retry after presentation refusal')
            assert bob.team_popup.identifier == rejected_id
            assert bob.team_popup.heading.text() == 'New team message'
            bob.team_popup.acknowledge_button.click()
            wait_for(lambda: inbox_row(bob, rejected_id).get('acknowledged'), 'retried message acknowledged')

            alice.show_notepad()
            window = alice.notepad
            window.new_entry('order')
            window.title.setText('Customer pickup order')
            window.customer.setText('Customer A')
            window.order_ref.setText('TEAM-001')
            window.order_status.setCurrentIndex(window.order_status.findData('preparing'))
            window.body.setPlainText('Prepare the parts, then call the customer.')
            window.item_text.setText('Brake pads')
            window.item_quantity.setValue(2)
            window.add_checklist_item()
            window.repeat.setValue(1)
            window.scheduled.setChecked(True)
            window.due.setDateTime(QDateTime.fromSecsSinceEpoch(int(clock.value + 120)))
            window.order_timed.setChecked(True)
            window.order_due.setDateTime(QDateTime.fromSecsSinceEpoch(int(clock.value + 3600)))
            window.team_everyone.setChecked(False)
            choose_recipient(window.team_members, bob_id)
            assert window.save_note()
            order_local_id = window.editing_id
            wait_for(lambda: bool(alice.notes_store.find(order_local_id).get('team_id')),
                     'customer order receives shared identity')
            order_id = alice.notes_store.find(order_local_id)['team_id']
            wait_for(lambda: note_by_team_id(bob, order_id) is not None and bob.team_popup.isVisible(),
                     'assigned customer note and popup reach Bob')
            assert bob.team_popup.identifier == order_id
            assert 'TEAM-001' in bob.team_popup.details.text()
            assert '2 × Brake pads' in bob.team_popup.body.toPlainText()
            assert not alice.team_popup.isVisible(), 'Customer reminder ignored its assignment'
            screenshot(window, 'team-customer-order.png')
            screenshot(bob.team_popup, 'team-customer-reminder.png')
            due = alice.notes_store.find(order_local_id)['team_due_at']
            wait_for(lambda: any(receipt.get('next_due') == due
                     for receipt in bob.team._receipts.get(order_id, [])),
                     'customer popup retains its intentional deadline')
            bob.team_popup.dismiss_button.click()
            clock.value = due + 1
            wait_for(lambda: bob.team_popup.isVisible(), 'assigned customer deadline reminder')
            assert bob.team_popup.heading.text() == 'Shared note reminder'
            window.done_button.click()
            wait_for(lambda: note_by_team_id(bob, order_id)['done'] and not bob.team_popup.isVisible(),
                     'Done cancels the remote customer popup')
            clock.value += 7200
            assert_no_popup(bob, 'A completed customer order still reminded its recipient')
            window.done_button.click()
            wait_for(lambda: not note_by_team_id(bob, order_id)['done'] and bob.team_popup.isVisible(),
                     'restoring an order starts a fresh reminder schedule')
            window.order_status.setCurrentIndex(window.order_status.findData('delivered'))
            assert window.save_note()
            wait_for(lambda: note_by_team_id(bob, order_id)['order_status'] == 'delivered'
                     and not bob.team_popup.isVisible(), 'Delivered cancels the remote customer popup')

            # Full text uses a shared blob and an independent local document DB.
            full_text = ('Customer handbook: pickup verification and daily desk tasks.\n' * 600).strip()
            prepared = alice.notes_store.documents.prepare_text(full_text, title='Team handbook')
            document = alice.note_service.add_prepared_document(prepared, 'Team handbook', 'local-handbook.pdf')
            wait_for(lambda: bool(document.get('team_id')), 'full document receives shared identity')
            document_id = document['team_id']
            wait_for(lambda: bool((note_by_team_id(bob, document_id) or {}).get('document_id')),
                     'full document blob indexed on the second PC')
            imported = note_by_team_id(bob, document_id)
            assert imported['body_characters'] == len(full_text)
            assert imported['body_sha256'] == document['body_sha256']
            assert imported['document_id'] != document['document_id']
            read_text = ''.join(page['text'] for page in bob.notes_store.documents.read_pages(imported['document_id']))
            assert read_text == full_text, 'Only the document preview was synchronized'
            assert_no_popup(bob, 'An unscheduled reference document created a reminder')

            alice.apply_settings({'quiet_mode': True})
            bob.apply_settings({'quiet_mode': True})
            daily = alice.note_service.save_note(None, 'Private customer desk routine',
                'Customer Secret needs a daily stock review.', 1440, due=clock.value + 86400,
                details={'kind': 'order', 'customer': 'Customer Secret', 'order_status': 'preparing',
                         'team_recipients': [bob_id]})
            # Protection applies immediately, before a worker publishes team IDs.
            assert 'Customer Secret' not in json.dumps(alice.ai_context(False, True))
            assert not any(entry['text'] == daily['title'] for entry in alice.memory_store.entries())
            wait_for(lambda: bool(daily.get('team_id')) and note_by_team_id(bob, daily['team_id']) is not None,
                     'daily work note synchronization')
            for buddy in (alice, bob):
                buddy.sync_task_memory()
                context = buddy.ai_context(False, True)
                serialized = json.dumps(context)
                assert 'Customer Secret' not in serialized and 'Private customer desk routine' not in serialized
                assert context['business_summary']['open_orders'] == 0
                assert not any(entry['source'] == 'task:' + daily['id']
                               or entry['text'] == daily['title'] for entry in buddy.memory_store.entries())
                assert str(shared) not in serialized

            shared_files = [path for path in shared.rglob('*') if path.is_file()]
            assert shared_files and not any(path.suffix in ('.sqlite', '.db') for path in shared_files)
            assert not any(path.name in ('settings.json', 'memory.json', 'notes.json') for path in shared_files)

            # Copied settings are display preferences, never employee identity.
            bob_settings = copy.deepcopy(bob.settings.values)
            bob_settings['team_employee_id'] = alice_id
            bob.settings.save(bob_settings)
            bob_profile = bob.team.profile_path.parent
            bob.shutdown()
            restored = create_buddy('bob', profile=bob_profile, copied=bob_settings)
            wait_for(lambda: bool(restored.team.identity()), 'restart with a copied settings employee ID')
            assert restored.team.identity()['employee_id'] == bob_id
            restored.shutdown()
            fresh = create_buddy('copied-app', copied=bob_settings)
            wait_for(lambda: bool(fresh.team.identity()), 'new PC with copied portable settings')
            assert fresh.team.identity()['employee_id'] not in (alice_id, bob_id)
            assert fresh.team.profile_path.parent != bob_profile
            assert not errors
        finally:
            for buddy in reversed(buddies):
                buddy.shutdown()
                assert not buddy.team.timer.isActive()
                assert not buddy.thread.isRunning()
                assert not buddy.team._thread.is_alive(), 'The temporary-folder sync worker did not stop'
            for widget in app.topLevelWidgets():
                widget.hide()
            app.processEvents()

    print('PASS: two-PC team setup, employee-targeted initial popup and timed reminder, read/dismiss, '
          'snooze/acknowledgement, quiet/busy/refused-popup delivery, shared customer orders, '
          'Done/Delivered cancellation, full document sync, default work AI/privacy, copied-settings '
          'identity isolation, local SQLite and bounded worker shutdown. No live Groq or SMB was used.')


if __name__ == '__main__':
    run()
