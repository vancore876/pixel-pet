"""Qt v5 checks using scripted native targets and Groq; no Windows claims."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QObject, Signal, QTimer, QRect, qInstallMessageHandler
from PySide6.QtWidgets import QApplication
from main import BuddyApp
from settings import AppSettings
from desktop_bridge import DesktopBridge
from ai_brain import AutonomousBrain
from credentials import CredentialStore
from ai_chat import ChatWindow


qInstallMessageHandler(lambda kind, context, text: print(text, file=sys.stderr) if 'This plugin does not support' not in text else None)
app = QApplication([])
app.setQuitOnLastWindowClosed(False)
temp = tempfile.TemporaryDirectory()
root = Path(temp.name)
buddy = BuddyApp(app, AppSettings(root / 'settings.json'), show_tray=False)
preview = Path(os.environ.get('BUDDY_QA_DIR', root))
preview.mkdir(parents=True, exist_ok=True)
errors, state = [], {}
targets = [{'id': 'real-folder', 'kind': 'folder', 'name': 'Projects', 'path': str(root), 'rect': [160, 170, 92, 92], 'hwnd': 2048, 'pid': 10, 'runtime': '42,10'},
           {'id': 'real-tab', 'kind': 'tab', 'name': 'Project notes — Browser', 'rect': [420, 150, 180, 34], 'hwnd': 3072, 'pid': 20, 'runtime': '42,20'},
           {'id': 'real-window', 'kind': 'window', 'name': 'JefferyNotes.txt — Notepad', 'rect': [120, 340, 460, 380], 'hwnd': 4096, 'pid': 30, 'runtime': ''}]
native_calls = []


class ScriptedClient(QObject):
    completed = Signal(object)
    failed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, content):
        super().__init__()
        self.content, self.calls, self.reply = content, [], None

    def send(self, model, messages, tools=True):
        self.calls.append({'model': model, 'messages': messages, 'tools': tools})
        self.busy_changed.emit(True)
        def done():
            self.busy_changed.emit(False)
            self.completed.emit({'content': self.content, 'tool_calls': []})
        QTimer.singleShot(50, done)
        return True

    def cancel(self):
        self.busy_changed.emit(False)


def native_request(action, payload, callback):
    native_calls.append((action, payload))
    if action == 'scan':
        result = {'targets': targets, 'monitors': []}
    elif action == 'replace_text':
        result = {'applied': True}
    elif action == 'select_tab':
        result = {'selected': True}
    else:
        result = state['selection']
    QTimer.singleShot(0, lambda: callback(result, ''))
    return True


buddy.desktop.bridge.request = native_request
buddy.desktop.bridge.supported = True


def begin():
    try:
        assert buddy.overlay.width() == 224 and buddy.overlay.height() <= 236
        buddy.pet.say('First line.\nSecond line.\nThird line.')
        assert buddy.pet.bubble.lines.cursor == 1
        assert buddy.pet.bubble.lines.timer.isActive()
        body = buddy.pet.bubble.lines
        state['bubble'] = body
        buddy.desktop.bridge.scanned({'targets': targets, 'monitors': []}, '')
        context = json.dumps(buddy.ai_context(False, False))
        assert 'real-folder' in context and 'Projects' not in context and str(root) not in context
        buddy.settings.values['ai_share_desktop_names'] = True
        assert 'Projects' in json.dumps(buddy.ai_context(False, False))
        buddy.settings.values['ai_share_desktop_names'] = False
        buddy.desktop.visit('real-folder')
        assert buddy.pet.hop_motion and buddy.pet.hop_target == 'desktop:real-folder'
        QTimer.singleShot(1400, folder_checks)
    except Exception as exc:
        errors.append(repr(exc)); app.quit()


def folder_checks():
    try:
        assert state['bubble'].cursor == 3, 'Speech was not revealed line by line'
        assert buddy.pet.hidden_in == 'desktop:real-folder' and not buddy.pet.isVisible()
        assert buddy.desktop.return_timer.isActive()
        buddy.desktop.peek()
        assert buddy.desktop.peek_sprite.isVisible()
        targets[0]['rect'][0] += 80
        buddy.desktop.bridge.scanned({'targets': targets, 'monitors': []}, '')
        assert buddy.desktop.peek_sprite.x() > 190, 'Peek did not follow the real icon coordinates'
        buddy.apply_settings({'pet_palette': 'sky'})
        assert not buddy.pet.isVisible(), 'Settings exposed a hidden pet'
        buddy.deliver_note({'id': 'test', 'title': 'Remember this', 'body': '', 'repeat_minutes': 5}, 'added')
        assert buddy.pet.isVisible() and not buddy.pet.hidden_in
        buddy.note_popup.hide()
        buddy.desktop.visit('real-tab')
        QTimer.singleShot(1300, tab_checks)
    except Exception as exc:
        errors.append(repr(exc)); app.quit()


def tab_checks():
    try:
        assert buddy.pet.action_state == 'BALANCE' and buddy.desktop.active_id == 'real-tab'
        targets[1]['rect'][0] += 60
        buddy.desktop.bridge.scanned({'targets': targets, 'monitors': []}, '')
        assert buddy.pet.x() >= 480
        assert not any(action == 'select_tab' for action, _ in native_calls), 'Riding a tab unexpectedly selected it'
        buddy.desktop.select_tab('real-tab')
        assert native_calls[-1][0] == 'select_tab' and native_calls[-1][1]['hwnd'] == 3072
        buddy.desktop.bridge.scanned({'targets': [targets[0]], 'monitors': []}, '')
        assert not buddy.desktop.active_id and not buddy.desktop.return_timer.isActive(), 'Closing a target stranded the pet'
        state['selection'] = {'text': 'HELLO', 'rects': [[200, 350, 65, 22]], 'hwnd': 4096, 'pid': 30, 'runtime': '42,30', 'capture_id': 'selection-test-1', 'editable': True, 'app': 'notepad'}
        buddy.desktop.captured(state['selection'], '')
        editor = buddy.desktop.text_window
        assert editor.letters.text() == 'HELLO' and not editor.apply_button.isEnabled()
        editor.letters.move_letter(0, 4)
        assert editor.letters.text() == 'ELLOH' and editor.apply_button.isEnabled()
        editor.apply()
        assert native_calls[-1][0] == 'replace_text'
        assert native_calls[-1][1]['original'] == 'HELLO' and native_calls[-1][1]['text'] == 'ELLOH'
        assert native_calls[-1][1]['hwnd'] == 4096
        assert native_calls[-1][1]['capture_id'] == 'selection-test-1'
        QTimer.singleShot(100, ai_checks)
    except Exception as exc:
        errors.append(repr(exc)); app.quit()


def ai_checks():
    try:
        editor = buddy.desktop.text_window
        assert 'Applied' in editor.status.text()
        editor.grab().save(str(preview / 'selected-letters.png'))
        editor.present({**state['selection'], 'editable': False})
        editor.letters.move_letter(0, 2)
        assert not editor.apply_button.isEnabled(), 'Read-only text became writable'
        editor.applied(None, 'The editor selection changed. Capture it again.')
        assert 'selection changed' in editor.status.text()
        buddy.desktop.bridge.scanned({'targets': targets, 'monitors': []}, '')
        buddy.desktop.show_panel()
        buddy.desktop.panel.grab().save(str(preview / 'real-desktop-targets.png'))
        buddy.desktop.panel.hide()
        editor.hide()
        buddy.pet.cancel_hop()
        buddy.pet.action_state = None
        key = CredentialStore(root / 'test.key')
        key.save('gsk_' + 'placeholder' * 3, remember=False)
        client = ScriptedClient('{"action":"animate","animation":"JUGGLE","say":"A tiny trick for you."}')
        state['client'] = client
        brain = AutonomousBrain(buddy.settings, key, buddy.ai_context, lambda: buddy.desktop.bridge.targets,
                     buddy.run_buddy_action, buddy.pet.say, lambda: True, client=client)
        state['brain'] = brain
        assert brain.request()
        assert client.calls[-1]['tools'] is False
        assert 'HELLO' not in json.dumps(client.calls[-1]['messages']), 'Selected text leaked into Groq context'
        reply = 'Hey! I’m here.\nYour notes are ready when you need them.\nI found a little place to perch.'
        chat_client = ScriptedClient(reply)
        chat = ChatWindow(buddy.settings, key, buddy.ai_context, buddy.run_buddy_action, client=chat_client)
        state['chat'], state['chat_client'] = chat, chat_client
        chat.show()
        chat.input.setText('Are you here, Jeffery?')
        chat.submit()
        assert chat_client.calls[-1]['tools'] is False
        if os.environ.get('BUDDY_QA_DIR'):
            frames = preview / 'line-frames'
            frames.mkdir(exist_ok=True)
            for index, milliseconds in enumerate(range(20, 700, 70)):
                QTimer.singleShot(milliseconds, lambda i=index: chat.grab().save(str(frames / f'line-{i:02d}.png')))
        QTimer.singleShot(700, final_checks)
    except Exception as exc:
        errors.append(repr(exc)); app.quit()


def final_checks():
    try:
        assert buddy.pet.action_state == 'JUGGLE', 'Groq did not influence the pet pose'
        chat = state['chat']
        body = chat.transcript.bodies[-1]
        assert body.cursor > 1, 'Chat did not reveal individual lines'
        chat.grab().save(str(preview / 'sliding-chat.png'))
        chat.tabs.setCurrentIndex(1)
        chat.grab().save(str(preview / 'groq-behavior.png'))
        buddy.dialog.refresh(2)
        buddy.dialog.show()
        app.processEvents()
        buddy.dialog.grab().save(str(preview / 'buddy-settings.png'))
        buddy.dialog.refresh(0)
        app.processEvents()
        buddy.dialog.grab().save(str(preview / 'settings.png'))
        buddy.dialog.hide()
        buddy.overlay.grab().save(str(preview / 'mini-hud.png'))
        brain = state['brain']
        brain.failed('Groq rejected the key. Save a working key in Connection.')
        count = len(state['client'].calls)
        assert brain.blocked and not brain.request() and len(state['client'].calls) == count
        brain.reset()
        assert not brain.blocked
        brain.stop()
        chat.close()
        buddy.desktop.return_timer.start(100)
        buddy.shutdown()
        assert not buddy.pet.timer.isActive() and not buddy.desktop.peek_timer.isActive()
        assert not buddy.desktop.random_timer.isActive() and not buddy.brain.timer.isActive()
        app.quit()
    except Exception as exc:
        errors.append(repr(exc)); app.quit()


QTimer.singleShot(100, begin)
QTimer.singleShot(10000, lambda: (errors.append('Desktop GUI check timed out'), app.quit()))
app.exec()
buddy.shutdown()
if 'brain' in state: state['brain'].stop()
if 'chat' in state: state['chat'].close()
temp.cleanup()
if errors: raise AssertionError('; '.join(errors))
print('PASS: sliding lines, tiny HUD, real-target hop/hide/peek/follow, target closure, reminder recovery, requested tab selection, selected-letter reorder/apply, read-only guard, Groq-driven pose, context privacy, 401 pause, cleanup. Native and Groq responses were scripted.')
