"""Conversation memory, notebook retrieval and canceled browsing integration."""
import json
from pathlib import Path
import tempfile
import unittest

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication
from ai_chat import ChatWindow
from memory import MemoryStore
from memory_learning import MemoryLearner
from notes import NoteStore
from settings import AppSettings
from memory_window import MemoryWindow

app = QApplication.instance() or QApplication([])


class Credentials:
    def get(self):
        return "scripted-session"


class Client(QObject):
    completed = Signal(object)
    failed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.calls = []
        self.reply = None

    def send(self, model, messages, tools=True, json_mode=False):
        self.calls.append({"messages": messages, "json_mode": json_mode, "tools": tools})
        return True

    def cancel(self):
        self.reply = None


class ChatMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = AppSettings(root / 'settings.json')
        self.store = MemoryStore(root / 'memory.json')
        self.notes = NoteStore(root / 'notes.json')
        self.client = Client()
        self.chat = ChatWindow(self.settings, Credentials(), lambda metrics, notes: {}, lambda action: 'ok',
            client=self.client, memory_store=self.store, notebook_store=self.notes)
        self.learner_client = Client()
        self.learner = MemoryLearner(self.store, self.settings, Credentials(), client=self.learner_client)
        self.chat.user_message.connect(self.learner.observe)

    def tearDown(self):
        self.learner.stop()
        self.chat.shutdown()
        self.chat.close()
        self.temp.cleanup()

    def test_preferences_are_persistent_and_used_in_same_turn(self):
        self.chat.input.setText('I like mint tea')
        self.chat.submit()
        reloaded = MemoryStore(self.store.path)
        self.assertEqual(reloaded.entries()[0]['text'], 'mint tea')
        self.assertIn('mint tea', self.client.calls[-1]['messages'][0]['content'])

    def test_completed_pdf_reference_is_found_near_end_and_cited(self):
        note = self.notes.add('Travel guide', 'intro ' * 1300 + 'The observatory opens at 18:30.', repeat=0,
                              details={'document_source': 'guide.pdf'})
        self.notes.complete(note['id'])
        self.chat.input.setText('When does the observatory open?')
        self.chat.submit()
        system = self.client.calls[-1]['messages'][0]['content']
        self.assertIn('18:30', system)
        self.assertIn('guide.pdf', system)
        self.assertIn('"done": true', system)

    def test_new_documents_do_not_crowd_preferences_out_of_context(self):
        self.store.remember('like', 'mint tea')
        for i in range(20):
            self.notes.add('Document ' + str(i), 'fresh reference material', repeat=0)
        self.chat.turn_query = 'Help me plan my day'
        self.assertIn('mint tea', self.chat.system_message()['content'])

    def test_sharing_off_excludes_preferences_and_notebook(self):
        self.store.remember('like', 'mint tea')
        self.notes.add('Private guide', 'hidden observatory details', repeat=0)
        self.settings.values['ai_share_memory'] = False
        self.chat.notes.setChecked(False)
        self.chat.input.setText('Hello')
        self.chat.submit()
        system = self.client.calls[-1]['messages'][0]['content']
        self.assertNotIn('mint tea', system)
        self.assertNotIn('hidden observatory', system)

    def test_groq_learning_requires_evidence_and_deleted_memory_stays_deleted(self):
        self.learner.observe('I like coffee')
        self.learner.pump()
        self.assertTrue(self.learner_client.calls[-1]['json_mode'])
        self.store.clear()
        self.learner.reset()
        self.learner_client.completed.emit({'content': json.dumps({'memories': [
            {'kind': 'like', 'text': 'coffee', 'evidence': 'I like coffee'}]})})
        self.assertEqual(self.store.entries(), [])

    def test_credentials_and_disabled_learning_are_not_sent_for_extraction(self):
        self.learner.observe('Remember my password is a-private-password')
        self.learner.pump()
        self.settings.values['memory_enabled'] = False
        self.learner.observe('I like tea')
        self.learner.pump()
        self.assertEqual(self.learner_client.calls, [])
        self.assertEqual(self.store.entries(), [])

    def test_web_sources_are_cited_and_require_explicit_toggle(self):
        self.chat.web_data = {'title': 'Opening hours', 'text': 'Open at 10.', 'url': 'https://example.com/hours',
                              'sources': [{'title': 'Opening hours', 'url': 'https://example.com/hours'}]}
        self.assertNotIn('Open at 10.', self.chat.system_message()['content'])
        self.chat.web_enabled.setChecked(True)
        system = self.chat.system_message()['content']
        self.assertIn('Open at 10.', system)
        self.assertIn('https://example.com/hours', system)

    def test_cancelled_browsing_does_not_leave_send_disabled(self):
        self.chat.web_loading = True
        self.chat.web_pending = 'opening hours'
        self.chat.set_busy(True)
        self.assertFalse(self.chat.send_button.isEnabled())
        self.chat.cancel_request()
        self.assertTrue(self.chat.send_button.isEnabled())
        self.assertIsNone(self.chat.web_pending)

    def test_older_groq_reply_cannot_reverse_newer_dislike(self):
        self.learner.observe('I like coffee')
        self.learner.pump()
        self.learner.observe("I don't like coffee")
        self.learner_client.completed.emit({'content': json.dumps({'memories': [
            {'kind': 'like', 'text': 'coffee', 'evidence': 'I like coffee'}]})})
        entry = self.store.entries()[0]
        self.assertEqual((entry['kind'], entry['text']), ('dislike', 'coffee'))

    def test_memory_editor_roundtrip_preserves_long_fact(self):
        entry = self.store.remember('fact', 'A personal fact: ' + 'x' * 430)
        window = MemoryWindow(self.store, self.settings)
        try:
            window.list.setCurrentRow(0)
            window.save_entry()
            self.assertEqual(self.store.entries()[0]['text'], entry['text'])
        finally:
            window.close()

    def test_web_completion_respects_withdrawn_sharing(self):
        self.chat.web_enabled.setChecked(False)
        self.chat.web_pending = 'opening hours'
        self.chat.web_received({'title': 'Hours', 'text': 'open at 10', 'sources': [], 'url': 'https://example.com'})
        self.assertFalse(self.chat.web_enabled.isChecked())
        self.assertNotIn('open at 10', self.client.calls[-1]['messages'][0]['content'])

    def test_reopening_chat_resumes_unfinished_reply(self):
        self.chat.transcript.add_message('Jeffery', '\n'.join(f'line {i}' for i in range(20)))
        body = self.chat.transcript.bodies[-1]
        self.chat.reject()
        self.assertFalse(body.timer.isActive())
        self.chat.show()
        self.assertTrue(body.timer.isActive())

    def test_completed_daily_task_is_forgotten_even_if_learning_is_off(self):
        from main import BuddyApp
        buddy = BuddyApp(app, self.settings, show_tray=False)
        try:
            note = buddy.note_service.save_note(None, 'Daily walk', 'Walk after work', 1440)
            self.assertTrue(any(e['text'] == 'Daily walk' for e in buddy.memory_store.entries()))
            buddy.settings.values['memory_enabled'] = False
            buddy.note_service.complete(note['id'])
            self.assertFalse(any(e['text'] == 'Daily walk' for e in buddy.memory_store.entries()))
        finally:
            buddy.shutdown()
            buddy.overlay.hide()
            buddy.pet.hide()

    def test_matching_task_completion_preserves_user_routine(self):
        from main import BuddyApp
        for source in ('manual', 'chat'):
            with self.subTest(source=source):
                buddy = BuddyApp(app, self.settings, show_tray=False)
                try:
                    saved = buddy.memory_store.remember('routine', 'Walk daily!', source=source)
                    note = buddy.note_service.save_note(None, 'walk DAILY', 'Walk after work', 1440)
                    self.assertEqual(buddy.memory_store.entries(kind='routine'), [saved])
                    buddy.note_service.complete(note['id'])
                    self.assertEqual(buddy.memory_store.entries(kind='routine'), [saved])
                    self.assertEqual(MemoryStore(buddy.memory_store.path).entries(kind='routine'), [saved])
                finally:
                    buddy.shutdown()
                    buddy.overlay.hide()
                    buddy.pet.hide()


if __name__ == '__main__':
    unittest.main()
