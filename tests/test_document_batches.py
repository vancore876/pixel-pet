"""Batched semantic indexing reads preserve bounded notebook integrity checks."""
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from documents import CHUNK_CHARS, DocumentCancelled, DocumentError, DocumentStore


class DocumentBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = DocumentStore(self.root / 'documents.sqlite')

    def tearDown(self):
        self.store.close(timeout=3)
        self.temp.cleanup()

    def import_text(self, text):
        source = self.root / 'source.txt'
        source.write_text(text, encoding='utf-8')
        return self.store.prepare_import({'text_file': source, 'title': 'Batch test'})['document_id']

    def test_read_batch_is_bounded_ordered_and_exact(self):
        text = 'é☕' * (CHUNK_CHARS * 18) + 'Final appointment'
        identifier = self.import_text(text)
        rows = self.store.read_pages(identifier, 1, 32)
        self.assertEqual([row['page'] for row in rows], list(range(1, 33)))
        self.assertEqual(''.join(row['text'] for row in rows), text[CHUNK_CHARS:33 * CHUNK_CHARS])
        self.assertEqual(self.store.read_pages(identifier, 1000), [])

    def test_empty_read_does_not_create_storage_and_invalid_ranges_fail(self):
        self.assertEqual(self.store.read_pages('a' * 32), [])
        self.assertFalse(self.store.path.exists())
        for start, limit in ((-1, 1), (True, 1), (0, 0), (0, 33), (0, True)):
            with self.assertRaises(DocumentError):
                self.store.read_pages('a' * 32, start, limit)

    def test_canceled_batch_reads_fail_before_reading(self):
        canceled = threading.Event()
        canceled.set()
        with self.assertRaises(DocumentCancelled):
            self.store.read_pages('a' * 32, cancel=canceled)
        self.assertFalse(self.store.path.exists())

    def test_missing_or_damaged_page_is_reported(self):
        identifier = self.import_text('x' * (CHUNK_CHARS * 3))
        with sqlite3.connect(self.store.path) as connection:
            connection.execute('DELETE FROM chunks WHERE document_id = ? AND page = 1', (identifier,))
        with self.assertRaises(DocumentError):
            self.store.read_pages(identifier)

    def test_deleted_documents_are_not_indexed(self):
        identifier = self.import_text('Personal appointment')
        self.store.delete(identifier)
        self.assertEqual(self.store.read_pages(identifier), [])
