"""Typo retrieval stays bounded and never changes saved text or executes commands."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fuzzy_search
from fuzzy_search import command_suggestions
from memory import MemoryStore
from notes import NoteStore


class FuzzyRecallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.memory = MemoryStore(root / 'memory.json')
        self.notes = NoteStore(root / 'notes.json')

    def tearDown(self):
        self.notes.close()
        self.temp.cleanup()

    def test_misspelled_preference_is_recalled_before_recent_unrelated_memory(self):
        coffee = self.memory.remember('like', 'Coffee after breakfast')
        self.memory.remember('fact', 'New library card')
        self.assertEqual(self.memory.entries(query='coffe', limit=1)[0]['id'], coffee['id'])
        self.assertEqual(self.memory.context('coffe', limit=1)['memories'][0]['id'], coffee['id'])
        self.assertEqual([row['id'] for row in self.memory.search_entries('coffe')], [coffee['id']])
        self.assertEqual(self.memory.search_entries('astronaut'), [])

    def test_preference_late_in_its_short_saved_text_remains_searchable(self):
        entry = self.memory.remember('fact', ' '.join(f'word{i}' for i in range(25)) + ' appointment')
        self.assertEqual(self.memory.search_entries('apointment')[0]['id'], entry['id'])

    def test_typo_titles_match_without_fuzzy_reading_full_document_text(self):
        document = self.notes.add('Dentist appointment', 'Reference. ' * 1500, repeat=0)
        other = self.notes.add('New grocery list', 'Bananas', repeat=0)
        with patch.object(self.notes.documents, 'iter_text', side_effect=AssertionError('Full text scanned')):
            self.assertEqual(self.notes.search_ids('apointment'), {document['id']})
            self.assertEqual(self.notes.recall_notes('apointment', limit=1)[0]['id'], document['id'])
        self.assertNotIn(other['id'], self.notes.search_ids('apointment'))
        self.assertLessEqual(len(self.notes.recall_notes('apointment')[0]['body']), 10000)

    def test_exact_title_matches_rank_before_approximate_matches(self):
        exact = self.notes.add('Dentist booking', 'Visit', repeat=0, now=100)
        self.notes.add('Dentst followup', 'Visit', repeat=0, now=200)
        self.assertEqual(self.notes.recall_notes('dentist', limit=1)[0]['id'], exact['id'])

    def test_secret_labels_are_not_approximate_candidates(self):
        self.notes.add('password appointment', 'Private note', repeat=0)
        with patch('notes.typo_score', wraps=fuzzy_search.typo_score) as score:
            self.assertEqual(self.notes.search_ids('apointment'), set())
        self.assertTrue(all(call.args[1] == '' for call in score.call_args_list))

    def test_optional_dependency_absent_preserves_exact_search_and_keyword_context(self):
        coffee = self.memory.remember('like', 'coffee')
        note = self.notes.add('Appointment', 'Dentist visit', repeat=0)
        with patch('fuzzy_search._process', None):
            self.assertEqual(self.memory.search_entries('coffee')[0]['id'], coffee['id'])
            self.assertEqual(self.memory.search_entries('cofee'), [])
            self.assertEqual(self.notes.search_ids('Appointment'), {note['id']})
            self.assertEqual(self.notes.search_ids('apointment'), set())
            self.assertEqual(self.memory.context('coffee')['memories'][0]['id'], coffee['id'])
            self.assertEqual(command_suggestions('waev', {'wave': 'wave'}), [])

    def test_short_words_and_suggestion_inputs_are_conservative(self):
        self.memory.remember('like', 'cats')
        self.assertEqual(self.memory.search_entries('car'), [])
        aliases = {'wave': 'wave', 'hide': 'hide', 'peek': 'peek'}
        before = copy.deepcopy(aliases)
        self.assertEqual(command_suggestions('waev', aliases), ['wave'])
        self.assertEqual(command_suggestions('x' * 10000, aliases), [])
        self.assertEqual(command_suggestions('waev', aliases, limit=0), [])
        self.assertEqual(aliases, before)

    def test_memory_editor_uses_typo_matches_and_removes_unrelated_rows(self):
        from PySide6.QtWidgets import QApplication
        from memory_window import MemoryWindow
        from settings import AppSettings
        app = QApplication.instance() or QApplication([])
        self.memory.remember('fact', 'Dentist appointment')
        self.memory.remember('like', 'Mint tea')
        window = MemoryWindow(self.memory, AppSettings(Path(self.temp.name) / 'settings.json'))
        try:
            window.search.setText('apointment')
            self.assertEqual(window.list.count(), 1)
            self.assertIn('Dentist appointment', window.list.item(0).text())
            window.search.setText('astronaut')
            self.assertEqual(window.list.count(), 0)
        finally:
            window.close()


class NotebookRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'notes.json'

    def tearDown(self):
        self.temp.cleanup()

    def test_later_invalid_record_does_not_leave_a_partial_writable_notebook(self):
        payload = {'notes': [{'id': 'valid', 'title': 'Keep original'},
                             {'id': 'invalid', 'body': 'x' * 10001}]}
        original = json.dumps(payload)
        self.path.write_text(original)
        store = NoteStore(self.path)
        self.assertEqual(store.notes, [])
        self.assertTrue(store.warning)
        self.assertEqual(self.path.with_suffix('.corrupt.json').read_text(), original)

    def test_repeated_corruption_preserves_every_backup(self):
        self.path.write_text('first damaged file')
        NoteStore(self.path)
        self.path.write_text('second damaged file')
        NoteStore(self.path)
        backups = list(self.path.parent.glob('notes.corrupt*.json'))
        self.assertEqual(len(backups), 2)
        self.assertEqual({p.read_text() for p in backups}, {'first damaged file', 'second damaged file'})

    def test_future_format_and_too_many_records_are_backed_up_without_truncation(self):
        for payload in ({'version': 99, 'notes': [{'id': 'n', 'title': 'Future data'}]},
                        {'notes': [{'id': str(i), 'title': 'Record'} for i in range(2001)]}):
            original = json.dumps(payload)
            self.path.write_text(original)
            store = NoteStore(self.path)
            self.assertTrue(store.warning)
            self.assertEqual(store.notes, [])
            self.assertIn(original, [p.read_text() for p in self.path.parent.glob('notes.corrupt*.json')])

    def test_failed_atomic_replace_retains_notebook_and_cleans_temporary_file(self):
        store = NoteStore(self.path)
        store.add('Original', 'Keep', repeat=0)
        original = self.path.read_bytes()
        with patch('notes.os.replace', side_effect=OSError('Disk full')):
            with self.assertRaises(OSError):
                store.add('New', 'Uncommitted', repeat=0)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual([n['title'] for n in store.notes], ['Original'])
        self.assertEqual(list(self.path.parent.glob('.notes-*.tmp')), [])


if __name__ == '__main__':
    unittest.main()
