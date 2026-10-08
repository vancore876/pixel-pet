"""The v5.1 notes workflow with scripted Groq; no live API key is used."""
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sys
import tempfile
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QObject, Signal, QTimer, qInstallMessageHandler
from PySide6.QtWidgets import QApplication
from main import BuddyApp
from notes import current_guidance
from settings import AppSettings

qInstallMessageHandler(lambda kind, context, text: print(text, file=sys.stderr) if 'This plugin does not support' not in text else None)
app = QApplication([])
app.setQuitOnLastWindowClosed(False)
temp = tempfile.TemporaryDirectory()
root = Path(temp.name)
preview = Path(os.environ.get('BUDDY_QA_DIR', root))
preview.mkdir(parents=True, exist_ok=True)
buddy = BuddyApp(app, AppSettings(root / 'settings.json'), show_tray=False)
errors, state = [], {}
due = (datetime.now().astimezone() + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0).isoformat()


class Credentials:
    def get(self): return 'scripted-test-session'


class ScriptedClient(QObject):
    completed = Signal(object)
    failed = Signal(str)
    def __init__(self):
        super().__init__()
        self.calls, self.generation = [], 0
    def send(self, model, messages, tools=True, json_mode=False):
        self.calls.append({'messages': messages, 'tools': tools, 'json_mode': json_mode})
        generation = self.generation
        day = datetime.fromisoformat(due).strftime('%a, %b %d')
        payload = {'reminder': 'Prepare the customer order for ' + day + ' at 9 AM.',
                   'next_step': 'Check stock, then pack the items for pickup.',
                   'suggested_due': due, 'reason': 'Based on tomorrow at 9 AM when the note was saved.'}
        QTimer.singleShot(180, lambda: self.completed.emit({'content': json.dumps(payload)}) if generation == self.generation else None)
        return True
    def cancel(self): self.generation += 1


client = ScriptedClient()
buddy.smart_notes.client.cancel()
buddy.smart_notes.client.completed.disconnect(buddy.smart_notes.received)
buddy.smart_notes.client.failed.disconnect(buddy.smart_notes.failed)
buddy.smart_notes.client = client
buddy.smart_notes.credentials = Credentials()
client.completed.connect(buddy.smart_notes.received)
client.failed.connect(buddy.smart_notes.failed)


def begin():
    try:
        buddy.apply_settings({'ai_autonomy': False, 'desktop_enabled': False, 'mouse_mode': 'off'})
        buddy.show_notepad()
        window = buddy.notepad
        window.title.setText('Prepare customer order')
        window.body.setPlainText('Customer pickup tomorrow at 9 AM. Check stock and pack the items.')
        window.repeat_choice.setCurrentIndex(window.repeat_choice.findData(0))
        window.quick_time(900)
        assert window.repeat.value() == 0 and window.scheduled.isChecked()
        assert window.save_note()
        state['id'] = window.editing_id
        state['original_due'] = buddy.notes_store.find(state['id'])['next_due']
        buddy.note_service.tick()
        assert buddy.note_popup.isVisible(), 'Ordinary reminder waited for Groq'
        assert not buddy.note_popup.guidance.isVisible()
        buddy.smart_notes.refresh()
        assert client.calls and client.calls[-1]['json_mode'] and not client.calls[-1]['tools']
        window.grab().save(str(preview / 'notepad.png'))
        QTimer.singleShot(550, advice_ready)
    except Exception as exc:
        errors.append(repr(exc)); app.quit()


def advice_ready():
    try:
        note = buddy.notes_store.find(state['id'])
        guidance = current_guidance(note)
        assert guidance and 'pack' in guidance['next_step']
        assert note['next_due'] == state['original_due'], 'Groq changed a reminder schedule'
        assert buddy.note_popup.guidance.isVisible()
        assert buddy.note_popup.guidance.text.startswith('Prepare the customer order')
        window = buddy.notepad
        window.show_section('advice')
        assert window.use_time.isVisible()
        window.grab().save(str(preview / 'smart-notes.png'))
        window.apply_suggested_time()
        assert window.dirty and note['next_due'] == state['original_due']
        assert window.save_note()
        assert abs(note['next_due'] - guidance['suggested_due']) < 1
        buddy.note_popup.snooze_minutes.setCurrentIndex(1)
        buddy.note_popup.snooze_button.click()
        assert not buddy.note_popup.isVisible()
        assert 895 < note['next_due'] - time.time() <= 900
        assert abs(window.due.dateTime().toSecsSinceEpoch() - note['next_due']) < 1, 'Notebook showed the old snooze time'
        buddy.deliver_note(note, 'reminder')
        QTimer.singleShot(1500, finish)
    except Exception as exc:
        errors.append(repr(exc)); app.quit()


def finish():
    try:
        buddy.note_popup.grab().save(str(preview / 'smart-reminder.png'))
        assert buddy.note_popup.guidance.cursor == len(buddy.note_popup.guidance.lines), 'Reminder lines did not finish sliding'
        assert 'next_reminder' in buddy.ai_context(False, True)['incomplete_notes'][0]
        window = buddy.notepad
        window.body.setPlainText('An unsaved draft must survive incoming text-file notes.')
        linked = root / 'JefferyNotes.txt'
        linked.write_text('Call the customer tomorrow at 9 AM\n', encoding='utf-8')
        buddy.note_service.link(linked)
        assert window.body.toPlainText().startswith('An unsaved draft')
        assert window.dirty
        window.search.setText('Call the customer')
        assert window.list.count() == 1
        buddy.smart_notes.refresh()
        assert any('Call the customer' in call['messages'][1]['content'] for call in client.calls) or buddy.smart_notes.queue
        buddy.notepad.smart_enabled.setChecked(False)
        assert not buddy.settings['ai_share_notes'] and not buddy.chat.notes.isChecked()
        assert buddy.smart_notes.pending is None and not buddy.note_popup.guidance.isVisible()
        buddy.note_popup.done_requested.emit(state['id'])
        assert buddy.notes_store.find(state['id'])['done']
        window.search.clear()
        window.filter.setCurrentIndex(2)
        assert window.list.count() == 1
        window.setup_ai.click()
        assert buddy.chat.isVisible() and buddy.chat.tabs.currentIndex() == 1
        buddy.shutdown()
        assert not buddy.smart_notes.timer.isActive()
        assert not buddy.smart_notes.debounce.isActive()
        app.quit()
    except Exception as exc:
        errors.append(repr(exc)); app.quit()


QTimer.singleShot(200, begin)
QTimer.singleShot(10000, app.quit)
app.exec()
buddy.shutdown()
temp.cleanup()
if errors: raise AssertionError('; '.join(errors))
assert not buddy.thread.isRunning()
print('PASS: note details to Groq, immediate offline reminder, sliding personalized advice, reviewed time suggestion, 15-minute snooze, search/filter, linked notes, preserved draft, sharing toggle, setup shortcut, shutdown. Groq replies were scripted.')
