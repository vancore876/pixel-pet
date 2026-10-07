"""Large imports, paged reading, metadata edits and full backups through Qt."""
from pathlib import Path
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from content_intake import _result, cleanup_result
from notes import NoteStore, NoteService
from notepad_window import NotepadWindow
from settings import AppSettings

app = QApplication.instance() or QApplication([])


def wait_until(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
    if not predicate():
        raise AssertionError('The notebook background operation did not finish.')


class ScaledNotepadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = AppSettings(self.root / 'settings.json')
        self.store = NoteStore(self.root / 'notes.json')
        self.service = NoteService(self.store, self.settings, lambda: False)
        self.window = NotepadWindow(self.service, self.settings)
        self.text = 'Document introduction.\n' + ('ordinary details ' * 9000).rstrip() + '\nAurora appointment at the north gate.'

    def tearDown(self):
        self.window.shutdown()
        self.window.close()
        self.temp.cleanup()

    def save_large_import(self):
        result = _result('pdf', 'Long appointment guide', self.text, filename='guide.pdf')
        self.window.imported(result)
        self.assertTrue(self.window.import_review.isReadOnly())
        self.assertEqual(self.window.save_import_button.text(), 'Save full document')
        self.assertTrue(self.window.save_import())
        wait_until(lambda: self.window.import_saved)
        self.assertFalse(Path(result['text_file']).exists())
        self.assertEqual(len(self.store.notes), 1)
        return self.store.notes[0]

    def test_full_import_preserves_draft_and_opens_bounded_pages(self):
        self.window.title.setText('Unfinished note')
        self.window.body.setPlainText('Preserve this draft.')
        note = self.save_large_import()
        self.assertEqual(self.window.body.toPlainText(), 'Preserve this draft.')
        self.assertEqual(note['body_characters'], len(self.text))
        self.assertLess(self.store.path.stat().st_size, 20000)
        self.window.dirty = False
        self.window.load_note(note['id'])
        self.assertTrue(self.window.body.isReadOnly())
        self.window.document_page.setValue(self.window.document_page.maximum())
        self.assertIn('Aurora appointment', self.window.body.toPlainText())
        self.assertLessEqual(len(self.window.body.toPlainText()), 10000)
        self.assertFalse(self.window.dirty)

    def test_title_and_schedule_edits_on_last_page_keep_all_text(self):
        note = self.save_large_import()
        self.window.load_note(note['id'])
        self.window.document_page.setValue(self.window.document_page.maximum())
        self.window.title.setText('Updated guide title')
        self.window.repeat.setValue(1440)
        self.assertTrue(self.window.save_note())
        restored = NoteStore(self.store.path)
        saved = restored.find(note['id'])
        self.assertEqual(saved['title'], 'Updated guide title')
        self.assertEqual(saved['repeat_minutes'], 1440)
        self.assertEqual(''.join(restored.documents.iter_text(saved['document_id'])), self.text)

    def test_explicit_preview_only_option_does_not_claim_full_import(self):
        with patch('content_intake.MAX_TEXT', 64):
            result = _result('pdf', 'Long guide', self.text)
        self.window.imported(result)
        self.window.import_preview_only.setChecked(True)
        self.assertFalse(self.window.import_review.isReadOnly())
        self.window.import_review.setPlainText('My chosen excerpt only.')
        self.assertTrue(self.window.save_import())
        self.assertEqual(self.store.notes[0]['body'], 'My chosen excerpt only.')
        self.assertFalse(self.store.notes[0].get('document_id'))
        self.assertFalse(Path(result['text_file']).exists())

    def test_search_finds_text_beyond_preview(self):
        note = self.save_large_import()
        self.assertNotIn('Aurora appointment', note['body'])
        self.window.search.setText('aurora')
        self.assertEqual(self.window.list.count(), 1)

    def test_chat_review_copy_has_independent_cleanup(self):
        result = _result('web', 'Reference', self.text, url='https://example.com')
        original = Path(result['text_file'])
        self.assertTrue(self.window.review_document(result))
        owned = Path(self.window.import_result['text_file'])
        self.assertNotEqual(owned, original)
        self.window.shutdown()
        self.assertFalse(owned.exists())
        self.assertTrue(original.exists())
        cleanup_result(result)

    def test_plain_export_and_zip_restore_include_document_tail(self):
        note = self.save_large_import()
        exported = self.root / 'export.txt'
        self.assertTrue(self.window.export_notes(str(exported)))
        wait_until(lambda: not self.window.transfer.busy)
        self.assertIn('Aurora appointment', exported.read_text(encoding='utf-8'))
        backup = self.root / 'backup.zip'
        self.assertTrue(self.window.export_backup(str(backup)))
        wait_until(lambda: not self.window.transfer.busy)
        self.service.delete(note['id'])
        self.assertTrue(self.window.import_backup(str(backup)))
        wait_until(lambda: not self.window.transfer.busy)
        self.assertEqual(len(self.store.notes), 1)
        restored = self.store.notes[0]
        self.assertEqual(''.join(self.store.documents.iter_text(restored['document_id'])), self.text)

    def test_mobile_json_refuses_incomplete_document_backup(self):
        self.save_large_import()
        target = self.root / 'mobile.json'
        self.assertFalse(self.window.export_backup(str(target)))
        self.assertFalse(target.exists())
        self.assertIn('ZIP', self.window.status.text())

    def test_new_long_note_switches_to_safe_paged_view(self):
        self.window.title.setText('A long manual note')
        self.window.body.setPlainText('saved content ' * 1500)
        self.assertTrue(self.window.save_note())
        self.assertTrue(self.window.body.isReadOnly())
        self.assertLessEqual(len(self.window.body.toPlainText()), 10000)
        self.assertTrue(self.store.notes[0].get('document_id'))

    def test_editing_existing_note_to_long_text_opens_paged_view(self):
        note = self.service.save_note(None, 'Originally short', 'Short body', 0)
        self.window.load_note(note['id'])
        text = 'converted full text ' * 1500
        self.window.body.setPlainText(text)
        self.assertTrue(self.window.save_note())
        self.assertTrue(self.window.body.isReadOnly())
        self.assertFalse(self.window.document_pages.isHidden())
        self.assertEqual(''.join(self.store.documents.iter_text(note['document_id'])), text.strip())

    def test_unavailable_document_keeps_preview_and_editor_state(self):
        note = self.save_large_import()
        with patch.object(self.store.documents, 'page_count', side_effect=ValueError('Unavailable storage')):
            self.window.load_note(note['id'])
        self.assertFalse(self.window.loading)
        self.assertTrue(self.window.body.isReadOnly())
        self.assertEqual(self.window.body.toPlainText(), note['body'])
        self.assertIn('could not be opened', self.window.status.text())

    def test_cleanup_failure_after_backup_commit_keeps_attached_text(self):
        attached = self.store.documents.prepare_text('Full archive content ' * 700)
        unused = self.store.documents.prepare_text('Unused archive content ' * 700)
        note = self.store.validate_note({'id': uuid.uuid4().hex, 'title': 'Restored reference',
            **attached, 'created': time.time(), 'updated': time.time(), 'repeat_minutes': 0})
        result = {'kind': 'backup_prepared', 'payload': {'notes': [note]},
            'prepared_ids': [attached['document_id'], unused['document_id']]}
        result['_cleanup'] = lambda: self.store._discard_unreferenced(result['prepared_ids'])
        original_delete = self.store.documents.delete

        def deleting(identifier):
            if identifier == unused['document_id']:
                raise ValueError('Storage is temporarily busy')
            return original_delete(identifier)

        with patch.object(self.store.documents, 'delete', side_effect=deleting):
            self.window.transfer_received(result)
        self.assertTrue(self.store.documents.exists(attached['document_id']))
        self.assertEqual(len(self.store.notes), 1)
        self.assertIn('Imported 1', self.window.status.text())
        self.assertNotIn('_cleanup', result)

    def test_changed_import_spool_is_rejected_without_saving(self):
        result = _result('pdf', 'Original reference', self.text)
        self.window.imported(result)
        Path(result['text_file']).write_text('Unexpected replacement content.', encoding='utf-8')
        self.assertTrue(self.window.save_import())
        wait_until(lambda: not self.window.intake.busy)
        self.assertEqual(self.store.notes, [])
        self.assertFalse(self.window.import_saved)
        self.assertIn('changed before saving', self.window.import_status.text())


if __name__ == '__main__':
    unittest.main()
