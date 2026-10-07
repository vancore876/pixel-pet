"""Full notebook text survives bounded previews, edits, and atomic failures."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from notes import MAX_DOCUMENT_CHARACTERS, NoteStore, note_fingerprint


class ScaledNotebookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = NoteStore(Path(self.temp.name) / 'notes.json')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def prepare(self, text):
        return self.store.documents.prepare_text(text, title='Reference document',
                                                other_note_characters=self.store.inline_character_count())

    def test_long_note_round_trip_keeps_full_text_outside_json(self):
        text = 'Ordinary reference paragraph.\n' * 4000 + 'The cobalt renewal deadline is November 18.'
        note = self.store.add('Long reference', text, repeat=0)
        self.assertLessEqual(len(note['body']), 10000)
        self.assertEqual(note['body_characters'], len(text))
        self.assertEqual(note['body_sha256'], hashlib.sha256(text.encode()).hexdigest())
        self.assertLess(self.store.path.stat().st_size, 15000)
        restored = NoteStore(self.store.path)
        self.assertEqual(restored.find(note['id'])['document_id'], note['document_id'])
        self.assertEqual(''.join(restored.documents.iter_text(note['document_id'])), text)
        self.assertIn('cobalt renewal deadline', restored.recall_notes('cobalt')[0]['body'])
        self.assertNotIn('cobalt renewal deadline', restored.find(note['id'])['body'])
        self.assertEqual(restored.search_ids('cobalt'), {note['id']})

    def test_edit_existing_inline_note_to_full_document_survives_restart(self):
        note = self.store.add('Original', 'A short note', repeat=0, now=100)
        text = 'Expanded reference paragraph.\n' * 1000 + 'Late turquoise appointment fact.'
        updated = self.store.edit(note['id'], 'Expanded', text, 0, now=200)
        self.assertEqual(updated['id'], note['id'])
        self.assertEqual(updated['body_characters'], len(text))
        self.assertEqual(updated['written'], 200)
        self.assertLessEqual(len(updated['body']), 10000)
        restored = NoteStore(self.store.path)
        self.assertEqual(len(restored.notes), 1)
        self.assertEqual(''.join(restored.documents.iter_text(updated['document_id'])), text)
        self.assertIn('turquoise appointment', restored.recall_notes('turquoise')[0]['body'])

    def test_large_reviewed_import_uses_one_document_without_reminders(self):
        text = 'x' * 2_010_000 + 'Unique late fact.'
        notes = self.store.add_documents([{'title': 'Book', 'body': text}],
                                        r'C:\Private\Library\book.pdf', now=100)
        self.assertEqual(len(notes), 1)
        note = notes[0]
        self.assertEqual(note['document_source'], 'book.pdf')
        self.assertEqual(note['source'], '')
        self.assertEqual(note['repeat_minutes'], 0)
        self.assertIsNone(note['next_due'])
        self.assertFalse(note['unannounced'])
        self.assertEqual(''.join(self.store.documents.iter_text(note['document_id'])), text)
        self.assertEqual(self.store.next_notification(1000000), (None, ''))

    def test_failed_attach_rolls_back_and_removes_new_unreferenced_text(self):
        self.store.add('Existing', 'Keep', repeat=0)
        previous = copy.deepcopy(self.store.state)
        document = self.prepare('Full document.\n' * 1000)
        with patch.object(self.store, 'save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.store.add_prepared_document(document, 'Incoming')
        self.assertEqual(self.store.state, previous)
        self.assertFalse(self.store.documents.exists(document['document_id']))
        self.assertEqual(NoteStore(self.store.path).notes, previous['notes'])

    def test_exact_billion_character_metadata_boundary_without_giant_fixture(self):
        identifier = 'a' * 32
        document = {'document_id': identifier, 'body_characters': MAX_DOCUMENT_CHARACTERS,
                    'body_sha256': 'b' * 64, 'body': 'Bounded preview'}
        with patch.object(self.store.documents, 'metadata', return_value=document):
            note = self.store.add_prepared_document(document, 'Capacity boundary')
        self.assertEqual(self.store.character_count(), 1_000_000_000)
        before = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.add('One more', 'x', repeat=0)
        self.assertEqual(len(self.store.notes), 1)
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(NoteStore(self.store.path).find(note['id'])['body_characters'], 1_000_000_000)

    def test_metadata_edits_preserve_text_and_reject_preview_replacements(self):
        text = 'Full document body.\n' * 1500
        note = self.store.add_prepared_document(self.prepare(text), 'Original', now=100)
        document_id = note['document_id']
        original_hash = note['body_sha256']
        self.store.edit(note['id'], 'Reviewed title', note['body'], 0, now=200)
        self.assertEqual(note['document_id'], document_id)
        self.assertEqual(note['body_sha256'], original_hash)
        with self.assertRaisesRegex(ValueError, 'read-only'):
            self.store.edit(note['id'], 'Reviewed title', 'Replace preview', 0)
        with self.assertRaisesRegex(ValueError, 'read-only'):
            self.store.modify(note['id'], body='Replace preview')
        self.assertEqual(''.join(self.store.documents.iter_text(document_id)), text)

    def test_delete_text_only_after_json_commit_and_last_reference(self):
        document = self.prepare('Shared full document.\n' * 1000)
        first = self.store.add_prepared_document(document, 'First')
        second = self.store.add_prepared_document(document, 'Second')
        self.assertEqual(self.store.character_count(), document['body_characters'])
        with patch.object(self.store, 'save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.store.delete(first['id'])
        self.assertTrue(self.store.documents.exists(document['document_id']))
        self.assertIsNotNone(self.store.find(first['id']))
        self.store.delete(first['id'])
        self.assertTrue(self.store.documents.exists(document['document_id']))
        self.store.delete(second['id'])
        self.assertFalse(self.store.documents.exists(document['document_id']))

    def test_json_preview_export_and_restore_are_explicitly_refused(self):
        note = self.store.add_prepared_document(self.prepare('Long reference.\n' * 1000), 'Reference')
        with self.assertRaisesRegex(ValueError, 'ZIP'):
            self.store.export_json()
        with self.assertRaisesRegex(ValueError, 'ZIP'):
            self.store.import_backup({'notes': [copy.deepcopy(note)]})
        self.assertEqual(self.store.import_backup({'notes': [copy.deepcopy(note)]}, allow_documents=True), 0)

    def test_backup_replacement_with_inline_note_removes_old_document_fields(self):
        document = self.prepare('Archived full reference.\n' * 1000)
        note = self.store.add_prepared_document(document, 'Old', now=100)
        incoming = {'id': note['id'], 'title': 'New', 'body': 'A short replacement', 'updated': 200}
        self.assertEqual(self.store.import_backup({'notes': [incoming]}), 1)
        self.assertNotIn('document_id', self.store.find(note['id']))
        self.assertFalse(self.store.documents.exists(document['document_id']))

    def test_fingerprint_distinguishes_equal_previews_with_different_full_text(self):
        preview = 'Shared introduction. ' * 500
        first = self.store.add('Reference', preview + 'First ending', repeat=0)
        second = self.store.add('Reference', preview + 'Second ending', repeat=0)
        self.assertEqual(first['body'], second['body'])
        self.assertNotEqual(note_fingerprint(first), note_fingerprint(second))

    def test_damaged_search_keeps_metadata_and_preview_available(self):
        note = self.store.add_prepared_document(self.prepare('Reference body.\n' * 1000), 'Reference')
        original = copy.deepcopy(note)
        with patch.object(self.store.documents, 'search', side_effect=ValueError('damaged index')):
            recalled = self.store.recall_notes('late fact')
            self.assertEqual(recalled[0]['body'], original['body'])
            self.assertIn('backup', self.store.warning)
            self.assertEqual(self.store.search_ids('Reference'), {note['id']})
        self.assertEqual(note, original)

    def test_invalid_document_reference_never_becomes_inline_note_on_reload(self):
        payload = {'notes': [{'id': 'bad', 'title': 'Reference', 'body': 'Preview',
                              'document_id': '../private', 'body_characters': 100,
                              'body_sha256': 'b' * 64}]}
        self.store.path.write_text(json.dumps(payload))
        restored = NoteStore(self.store.path)
        self.assertTrue(restored.warning)
        self.assertEqual(restored.notes, [])
        self.assertTrue(self.store.path.with_suffix('.corrupt.json').exists())


if __name__ == '__main__':
    unittest.main()
