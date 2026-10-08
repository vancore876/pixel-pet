"""Reviewed document imports are durable, bounded, and keep writing drafts safe."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QMessageBox
from notes import NoteService, NoteStore
from notepad_window import NotepadWindow
from settings import AppSettings
from smart_notes import SmartNoteAssistant


class ScriptedIntake(QObject):
    completed = Signal(object)
    failed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.busy = False
        self.calls = []
        self.pdf_options = {}
        self.closed = False

    def begin(self, kind, value):
        if self.busy or self.closed:
            return False
        self.busy = True
        self.calls.append((kind, value))
        self.busy_changed.emit(True)
        return True

    def import_pdf(self, path, **options):
        self.pdf_options = options
        return self.begin('pdf', path)

    def fetch_url(self, url):
        return self.begin('web', url)

    def finish(self, result):
        self.busy = False
        self.busy_changed.emit(False)
        self.completed.emit(result)

    def cancel(self):
        self.busy = False
        self.busy_changed.emit(False)

    def shutdown(self):
        self.closed = True
        self.cancel()


def document(kind='pdf'):
    return {'kind': kind, 'title': 'Appointment details',
        'text': 'Bring your ID.\n\nAppointment: 8 October at 9 AM.',
        'url': 'https://example.org/appointments' if kind == 'web' else '',
        'sections': [{'title': 'Appointment details · 1/2', 'body': 'Bring your ID.'},
                     {'title': 'Appointment details · 2/2', 'body': 'Appointment: 8 October at 9 AM.'}],
        'sources': [{'title': 'Appointment details', 'url': 'https://example.org/appointments'}] if kind == 'web' else [],
        'truncated': False}


class NotebookImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = NoteStore(root / 'notes.json')
        self.settings = AppSettings(root / 'settings.json')
        self.service = NoteService(self.store, self.settings)
        self.patcher = patch('notepad_window.IntakeService', ScriptedIntake)
        self.patcher.start()
        self.window = NotepadWindow(self.service, self.settings)

    def tearDown(self):
        self.window.shutdown()
        self.window.close()
        self.service.stop()
        self.patcher.stop()
        self.temp.cleanup()

    def test_large_document_is_split_and_reloaded_without_timed_schedule(self):
        text = 'x' * 25000
        with patch.object(self.store, 'save', wraps=self.store.save) as save:
            added = self.store.add_documents([{'title': 'Long PDF', 'body': text}],
                r'C:\Private\Medical\appointment.pdf', now=100)
        self.assertEqual(save.call_count, 1)
        self.assertEqual(''.join(note['body'] for note in added), text)
        self.assertEqual(len(added), 3)
        restored = NoteStore(self.store.path).notes
        self.assertTrue(all(len(note['body']) <= 10000 for note in restored))
        self.assertTrue(all(note['repeat_minutes'] == 0 and note['next_due'] is None for note in restored))
        self.assertTrue(all(note['source'] == '' and note['document_source'] == 'appointment.pdf' for note in restored))
        self.assertTrue(all(not note['unannounced'] for note in restored))
        self.assertEqual(self.store.next_notification(1000000), (None, ''))

    def test_invalid_or_full_import_does_not_partially_save(self):
        self.store.add('Existing', 'Keep this', repeat=0)
        previous = copy.deepcopy(self.store.state)
        for incoming in ([{'title': 'Valid', 'body': 'Text'}, {'body': 42}],
                         [{'body': ' '}]):
            with self.assertRaises(ValueError):
                self.store.add_documents(incoming)
            self.assertEqual(self.store.state, previous)
        with patch('notes.MAX_DOCUMENT_CHARACTERS', 500000):
            with self.assertRaises(ValueError):
                self.store.add_documents([{'title': 'Too much', 'body': 'x' * 500001}])
        self.assertEqual(self.store.state, previous)
        with patch('notes.MAX_NOTES', len(self.store.notes)):
            with self.assertRaises(ValueError):
                self.store.add_documents(document()['sections'])
        self.assertEqual(self.store.state, previous)
        self.assertEqual(NoteStore(self.store.path).state['notes'], previous['notes'])

    def test_failed_write_rolls_back_every_section_and_emits_no_change(self):
        self.store.add('Existing', 'Keep this', repeat=0)
        previous = copy.deepcopy(self.store.state)
        changes = []
        self.service.changed.connect(changes.append)
        with patch.object(self.store, 'save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.service.add_documents(document()['sections'])
        self.assertEqual(self.store.state, previous)
        self.assertEqual(changes, [])
        self.assertEqual(NoteStore(self.store.path).notes, previous['notes'])

    def test_review_save_preserves_current_note_and_unsaved_draft(self):
        original = self.service.save_note(None, 'Call the plumber', 'Fix the sink', 30)
        self.window.load_note(original['id'])
        self.window.body.setPlainText('Unsaved draft with private details')
        existing = copy.deepcopy(original)
        self.window.import_pdf('/tmp/appointment.pdf')
        self.assertFalse(self.window.import_review.isEnabled())
        self.assertFalse(self.window.save_import_button.isEnabled())
        self.window.intake.finish(document())
        self.assertEqual(len(self.store.notes), 1, 'Extracting saved notes before review')
        self.window.show_section('import')
        self.assertTrue(self.window.save_current_tab())
        self.assertEqual(self.window.editing_id, original['id'])
        self.assertEqual(self.window.body.toPlainText(), 'Unsaved draft with private details')
        self.assertTrue(self.window.dirty)
        self.assertEqual(self.store.find(original['id']), existing)
        self.assertEqual(len(self.store.notes), 3)
        self.assertFalse(self.window.save_import(), 'Saving the same preview twice duplicated notes')
        # Workflows navigate semantic sections rather than fixed tab positions.
        self.assertEqual(self.window.editor_tabs.tabText(self.window.section_indices['orders']), 'Orders')
        self.assertEqual(self.window.editor_tabs.tabText(self.window.section_indices['checklists']), 'Checklists')
        self.assertEqual(self.window.editor_tabs.tabText(self.window.section_indices['import']), 'Sources / Import')
        self.window.dirty = False

    def test_edited_review_saves_only_reviewed_content_and_keeps_web_source(self):
        self.window.web_address.setText('https://example.org/appointments')
        self.window.read_webpage()
        self.assertEqual(self.window.intake.calls, [('web', 'https://example.org/appointments')])
        self.window.intake.finish(document('web'))
        self.window.import_review.setPlainText('Bring a driving licence instead.')
        self.window.import_title.setText('Reviewed appointment')
        changes = []
        self.service.changed.connect(changes.append)
        self.assertTrue(self.window.save_import())
        self.assertEqual(changes, [''])
        note = self.store.notes[0]
        self.assertEqual((note['title'], note['body']), ('Reviewed appointment', 'Bring a driving licence instead.'))
        self.assertEqual(note['document_source'], 'https://example.org/appointments')
        self.assertEqual(note['source'], '')
        self.assertEqual(len(self.store.notes), 1)
        self.window.load_note(note['id'])
        self.assertIn('https://example.org/appointments', self.window.document_origin.text())

    def test_cancel_and_shutdown_leave_notebook_intact(self):
        self.window.import_pdf('/tmp/appointment.pdf')
        self.window.cancel_import()
        self.assertFalse(self.window.intake.busy)
        self.assertTrue(self.window.import_pdf_button.isEnabled())
        self.assertEqual(self.store.notes, [])
        self.assertIn('cancelled', self.window.import_status.text())
        opened = []
        self.window.chat_requested.connect(lambda: opened.append(True))
        self.window.talk_button.click()
        self.assertEqual(opened, [True])
        self.window.shutdown()
        self.assertTrue(self.window.intake.closed)

    def test_chat_source_review_preserves_unsaved_preview_and_cancels_old_reader(self):
        self.window.imported(document())
        self.window.import_review.setPlainText('My reviewed selection must survive.')
        with patch('notepad_window.QMessageBox.question', return_value=QMessageBox.No):
            self.assertFalse(self.window.review_document(document('web')))
        self.assertEqual(self.window.import_review.toPlainText(), 'My reviewed selection must survive.')
        self.assertTrue(self.window.save_import())
        self.window.import_pdf('/tmp/current-reader.pdf')
        self.assertTrue(self.window.intake.busy)
        self.assertTrue(self.window.review_document(document('web')))
        self.assertFalse(self.window.intake.busy)
        self.assertEqual(self.window.import_result['kind'], 'web')
        self.assertEqual(self.window.editor_tabs.currentIndex(), self.window.section_indices['import'])

    def test_imported_sections_reach_smart_reminders_without_changing_schedule(self):
        class Client(QObject):
            completed = Signal(object)
            failed = Signal(str)
            def __init__(self):
                super().__init__()
                self.calls = []
            def send(self, model, messages, tools=True, json_mode=False):
                self.calls.append(messages)
                return True
            def cancel(self):
                pass
        class Credentials:
            def get(self):
                return 'scripted-key'
        client = Client()
        assistant = SmartNoteAssistant(self.service, self.settings, Credentials(), client=client)
        try:
            added = self.service.add_documents(document()['sections'], 'Appointment.pdf')
            self.assertTrue(assistant.debounce.isActive())
            assistant.refresh()
            self.assertTrue(client.calls)
            self.assertIn('Bring your ID.', str(client.calls))
            self.assertTrue(all(note['next_due'] is None for note in added))
        finally:
            assistant.stop()


if __name__ == '__main__':
    unittest.main()
