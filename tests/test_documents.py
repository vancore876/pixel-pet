import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from documents import (CHUNK_CHARS, EXCERPT_CHARS, MAX_NOTEBOOK_CHARACTERS,
                       DocumentCancelled, DocumentError, DocumentStore)


class DocumentStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.store = DocumentStore(self.root / "state" / "documents.sqlite")

    def tearDown(self):
        self.assertTrue(self.store.stop_cleanup(timeout=3))
        self.directory.cleanup()

    def source(self, text, name="source.txt"):
        path = self.root / name
        with path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        return path

    def test_public_capacity_and_empty_reads_do_not_create_database(self):
        self.assertEqual(MAX_NOTEBOOK_CHARACTERS, 1_000_000_000)
        identifier = "a" * 32
        self.assertEqual(self.store.metadata(identifier), {})
        self.assertEqual(self.store.read_page(identifier, 0), "")
        self.assertEqual(self.store.page_count(identifier), 0)
        self.assertEqual(list(self.store.iter_text(identifier)), [])
        self.assertEqual(self.store.search("appointment"), [])
        self.assertEqual(self.store.total_characters(), 0)
        self.assertFalse(self.store.delete(identifier))
        self.assertFalse(self.store.path.parent.exists())

    def test_utf8_pages_and_reopened_store_retain_exact_text(self):
        text = ("Café ☕ — 日本語\r\n" * 2000) + "Remember the dentist on Friday."
        prepared = self.store.prepare_import({"text_file": self.source(text), "title": "Travel"})
        identifier = prepared["document_id"]
        self.assertEqual(prepared["body_characters"], len(text))
        self.assertEqual(prepared["body_sha256"], hashlib.sha256(text.encode("utf-8")).hexdigest())
        self.assertEqual(prepared["body"], text[:CHUNK_CHARS])
        reopened = DocumentStore(self.store.path)
        self.assertEqual(reopened.metadata(identifier), prepared)
        self.assertTrue(reopened.exists(identifier))
        self.assertEqual("".join(reopened.iter_text(identifier)), text)
        self.assertEqual(reopened.total_characters(), len(text))
        self.assertEqual(reopened.page_count(identifier), (len(text) + CHUNK_CHARS - 1) // CHUNK_CHARS)
        for page in range(reopened.page_count(identifier)):
            self.assertEqual(reopened.read_page(identifier, page),
                             text[page * CHUNK_CHARS:(page + 1) * CHUNK_CHARS])
        self.assertEqual(reopened.read_page(identifier, reopened.page_count(identifier)), "")

    def test_file_import_reads_only_bounded_chunks(self):
        path = self.source("Bounded input. " * 2000)
        original = Path.open
        reads = []

        class CheckedFile:
            def __init__(self, handle):
                self.handle = handle

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.handle.close()

            def read(self, size=-1):
                reads.append(size)
                if not 0 < size <= CHUNK_CHARS:
                    raise AssertionError("Whole-file reads are forbidden")
                return self.handle.read(size)

        def checked_open(selected, *args, **kwargs):
            handle = original(selected, *args, **kwargs)
            return CheckedFile(handle) if selected == path else handle

        with patch.object(Path, "open", checked_open):
            prepared = self.store.prepare_import({"text_file": path})
        self.assertGreater(len(reads), 2)
        self.assertEqual(prepared["body_characters"], path.stat().st_size)

    def test_quota_includes_other_notes_and_stored_documents(self):
        with patch("documents.MAX_NOTEBOOK_CHARACTERS", 100):
            first = self.store.prepare_text("x" * 40, other_note_characters=10)
            second = self.store.prepare_text("y" * 40, other_note_characters=20)
            self.assertEqual(self.store.total_characters(), 80)
            with self.assertRaisesRegex(DocumentError, "capacity"):
                self.store.prepare_text("z", other_note_characters=20)
            self.assertEqual(self.store.total_characters(), 80)
            self.assertTrue(self.store.delete(first["document_id"]))
            third = self.store.prepare_text("z" * 40, other_note_characters=20)
            self.assertEqual(self.store.total_characters(), 80)
            self.assertTrue(self.store.exists(second["document_id"]))
            self.assertTrue(self.store.exists(third["document_id"]))

    def test_overflow_rolls_back_all_chunks_and_search_index(self):
        first = self.store.prepare_text("Existing dentist appointment.")
        with patch("documents.MAX_NOTEBOOK_CHARACTERS", CHUNK_CHARS + 100):
            with self.assertRaisesRegex(DocumentError, "capacity"):
                self.store.prepare_import({"text_file": self.source("newtoken " * 2000)})
        self.assertEqual(self.store.total_characters(), first["body_characters"])
        self.assertEqual(self.store.search("newtoken"), [])
        self.assertEqual(len(self.store.search("dentist")), 1)

    def test_cancel_during_import_rolls_back(self):
        cancel = threading.Event()
        path = self.source("cancelledtext " * 2000)
        original_check = __import__("documents")._check_cancel
        calls = 0

        def cancel_later(event):
            nonlocal calls
            calls += 1
            if calls == 4:
                event.set()
            original_check(event)

        with patch("documents._check_cancel", side_effect=cancel_later):
            with self.assertRaises(DocumentCancelled):
                self.store.prepare_import({"text_file": path}, cancel=cancel)
        self.assertEqual(self.store.total_characters(), 0)
        self.assertEqual(self.store.search("cancelledtext"), [])

    def test_invalid_utf8_and_blank_documents_do_not_commit(self):
        path = self.root / "invalid.txt"
        path.write_bytes(b"Valid text. " * 2000 + b"\xff")
        with self.assertRaisesRegex(DocumentError, "UTF-8"):
            self.store.prepare_import({"text_file": path})
        self.assertEqual(self.store.total_characters(), 0)
        with self.assertRaisesRegex(DocumentError, "no text"):
            self.store.prepare_text(" \n\t" * 4000)
        self.assertEqual(self.store.total_characters(), 0)
        with self.assertRaisesRegex(DocumentError, "UTF-8"):
            self.store.prepare_text("text\ud800")
        self.assertEqual(self.store.total_characters(), 0)

    def test_full_text_recall_finds_beyond_preview_and_filters_documents(self):
        text = "Ordinary introductory content. " * 5000 + "\nSecret appointment: orthodontist on Tuesday."
        first = self.store.prepare_text(text)
        second = self.store.prepare_text("Different orthodontist, Friday.")
        hits = self.store.search("orthodontist", document_ids=[first["document_id"]])
        self.assertEqual(len(hits), 1)
        self.assertGreater(hits[0]["page"], 0)
        self.assertIn("orthodontist", hits[0]["text"])
        self.assertIn("Tuesday", hits[0]["text"])
        self.assertLessEqual(len(hits[0]["text"]), EXCERPT_CHARS)
        self.assertEqual(hits[0]["document_id"], first["document_id"])
        self.assertEqual(self.store.search("orthodontist", document_ids=[]), [])
        self.assertEqual(len(self.store.search("orthodontist")), 2)
        self.assertEqual(len(self.store.search("orthodontist", limit=1)), 1)
        self.assertEqual(self.store.search(" \" ( ) * ^ : NEAR("), [])
        self.assertIsInstance(self.store.search("' OR 1=1; DROP TABLE documents;--"), list)
        self.assertTrue(self.store.exists(second["document_id"]))

    def test_natural_questions_recall_the_complete_adjacent_fact(self):
        text = ("Guide introduction and background material. " * 2800
                + "\nThe observatory opens at 18:30 and its entrance is the north gate.\n")
        prepared = self.store.prepare_text(text)
        hits = self.store.search("When does the observatory open and where is its entrance?", limit=12)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["document_id"], prepared["document_id"])
        self.assertIn("18:30", hits[0]["text"])
        self.assertIn("north gate", hits[0]["text"])
        self.assertLessEqual(len(hits[0]["text"]), EXCERPT_CHARS)

    def test_long_context_tokens_do_not_hide_the_matching_fact(self):
        self.store.prepare_text("x" * 5000 + " The observatory entrance is the north gate.")
        hits = self.store.search("observatory entrance")
        self.assertEqual(len(hits), 1)
        self.assertIn("north gate", hits[0]["text"])
        self.assertLessEqual(len(hits[0]["text"]), EXCERPT_CHARS)

    def test_parallel_imports_cannot_exceed_capacity(self):
        self.store.prepare_text("Existing")

        def import_document(index):
            try:
                return self.store.prepare_text(str(index) * 70)
            except DocumentError:
                return None

        with patch("documents.MAX_NOTEBOOK_CHARACTERS", 100):
            with ThreadPoolExecutor(max_workers=3) as executor:
                results = list(executor.map(import_document, range(3)))
            self.assertEqual(sum(result is not None for result in results), 1)
            self.assertEqual(self.store.total_characters(), 78)

    def test_delete_removes_full_text_and_is_idempotent(self):
        prepared = self.store.prepare_text("Delete this dentist appointment.")
        self.assertTrue(self.store.delete(prepared["document_id"]))
        self.assertFalse(self.store.delete(prepared["document_id"]))
        self.assertEqual(self.store.metadata(prepared["document_id"]), {})
        self.assertEqual(self.store.search("dentist"), [])
        self.assertEqual(self.store.total_characters(), 0)

    def test_delete_fails_immediately_while_an_import_holds_the_writer(self):
        prepared = self.store.prepare_text("Keep this document until the import is finished.")
        with sqlite3.connect(self.store.path, isolation_level=None) as writer:
            writer.execute("BEGIN IMMEDIATE")
            try:
                started = time.monotonic()
                with self.assertRaises(DocumentError):
                    self.store.delete(prepared["document_id"])
                self.assertLess(time.monotonic() - started, 1.0)
                self.assertEqual(self.store.metadata(prepared["document_id"]), prepared)
            finally:
                writer.execute("ROLLBACK")
        self.assertTrue(self.store.delete(prepared["document_id"]))

    def test_large_delete_is_logical_then_purged_in_bounded_batches(self):
        prepared = self.store.prepare_text("A glossary entry and its explanation.\n" * 30_000)
        with patch.object(self.store, "_schedule_cleanup"):
            started = time.monotonic()
            self.assertTrue(self.store.delete(prepared["document_id"]))
            self.assertLess(time.monotonic() - started, 1.0)
        self.assertFalse(self.store.exists(prepared["document_id"]))
        self.assertEqual(self.store.read_page(prepared["document_id"], 0), "")
        self.assertEqual(self.store.search("glossary"), [])
        self.assertEqual(self.store.matching_ids("glossary"), set())
        self.assertEqual(self.store.total_characters(), 0)
        with sqlite3.connect(self.store.path) as connection:
            previous = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        self.assertEqual(previous, prepared["page_count"])
        while self.store._cleanup_batch():
            with sqlite3.connect(self.store.path) as connection:
                remaining = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            self.assertLessEqual(previous - remaining, 32)
            previous = remaining
        self.assertEqual(previous, 0)

    def test_reader_snapshot_survives_logical_delete_and_physical_cleanup(self):
        text = "A preserved reader snapshot.\n" * 1200
        prepared = self.store.prepare_text(text)
        reader = self.store.iter_text(prepared["document_id"])
        first = next(reader)
        with patch.object(self.store, "_schedule_cleanup"):
            self.store.delete(prepared["document_id"])
        while self.store._cleanup_batch():
            pass
        self.assertEqual(first + "".join(reader), text)
        self.assertFalse(self.store.exists(prepared["document_id"]))

    def test_in_progress_export_keeps_its_snapshot_during_cleanup(self):
        text = "A preserved backup snapshot.\n" * 1200
        prepared = self.store.prepare_text(text)
        original = self.store._iter_checked
        started, resume = threading.Event(), threading.Event()
        target = self.root / "concurrent-backup.txt"

        def held_reader(connection, metadata):
            for index, chunk in enumerate(original(connection, metadata)):
                if index == 1:
                    started.set()
                    if not resume.wait(3):
                        raise AssertionError("The cleanup did not release the backup")
                yield chunk

        with patch.object(self.store, "_iter_checked", side_effect=held_reader):
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self.store.export_document, prepared["document_id"], target)
                try:
                    self.assertTrue(started.wait(3))
                    with patch.object(self.store, "_schedule_cleanup"):
                        self.store.delete(prepared["document_id"])
                    while self.store._cleanup_batch():
                        pass
                finally:
                    resume.set()
                self.assertEqual(future.result(timeout=3), prepared)
        self.assertEqual(target.read_bytes(), text.encode("utf-8"))

    def test_interrupted_cleanup_resumes_when_store_reopens(self):
        prepared = self.store.prepare_text("Reclaim this document later.\n" * 1500)
        with patch.object(self.store, "_schedule_cleanup"):
            self.store.delete(prepared["document_id"])
        reopened = DocumentStore(self.store.path)
        deadline = time.monotonic() + 3
        remaining = 1
        while remaining and time.monotonic() < deadline:
            with sqlite3.connect(self.store.path) as connection:
                remaining = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            if remaining:
                time.sleep(0.02)
        self.assertEqual(remaining, 0)
        self.assertFalse(reopened.exists(prepared["document_id"]))

    def test_cleanup_does_not_recreate_a_removed_database(self):
        with self.assertRaises(DocumentError):
            self.store._cleanup_batch()
        self.assertFalse(self.store.path.parent.exists())

    def test_close_prevents_cleanup_writes_until_a_new_store_is_opened(self):
        prepared = self.store.prepare_text("Keep pending cleanup recoverable.\n" * 1000)
        with patch.object(self.store, "_schedule_cleanup"):
            self.store.delete(prepared["document_id"])
        self.assertTrue(self.store.close())
        self.store._schedule_cleanup()
        time.sleep(0.03)
        with sqlite3.connect(self.store.path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
                             prepared["page_count"])
        reopened = DocumentStore(self.store.path)
        try:
            deadline = time.monotonic() + 3
            remaining = 1
            while remaining and time.monotonic() < deadline:
                with sqlite3.connect(self.store.path) as connection:
                    remaining = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
                if remaining:
                    time.sleep(0.02)
            self.assertEqual(remaining, 0)
        finally:
            self.assertTrue(reopened.stop_cleanup(timeout=3))

    def test_stop_cleanup_interrupts_a_worker_waiting_for_a_writer(self):
        prepared = self.store.prepare_text("Cleanup waits without blocking shutdown.")
        with patch.object(self.store, "_schedule_cleanup"):
            self.store.delete(prepared["document_id"])
        with sqlite3.connect(self.store.path, isolation_level=None) as writer:
            writer.execute("BEGIN IMMEDIATE")
            try:
                self.store._schedule_cleanup()
                started = time.monotonic()
                self.assertTrue(self.store.stop_cleanup(timeout=0.5))
                self.assertLess(time.monotonic() - started, 0.5)
            finally:
                writer.execute("ROLLBACK")

    def test_version_one_documents_remain_readable_and_upgrade_on_write(self):
        prepared = self.store.prepare_text("Earlier document format remains intact.")
        with sqlite3.connect(self.store.path) as connection:
            connection.execute("ALTER TABLE documents DROP COLUMN deleted")
            connection.execute("PRAGMA user_version = 1")
        with patch.object(DocumentStore, "_schedule_cleanup"):
            reopened = DocumentStore(self.store.path)
        self.assertEqual(reopened.metadata(prepared["document_id"]), prepared)
        self.assertEqual(reopened.matching_ids("earlier"), {prepared["document_id"]})
        new = reopened.prepare_text("A new document after upgrading.")
        self.assertEqual(reopened.metadata(prepared["document_id"]), prepared)
        self.assertTrue(reopened.exists(new["document_id"]))
        with sqlite3.connect(self.store.path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 3)

    def test_version_two_read_only_pages_and_totals_upgrade_only_on_write(self):
        prepared = self.store.prepare_text("A version two notebook stays readable.")
        with sqlite3.connect(self.store.path) as connection:
            connection.execute("ALTER TABLE documents DROP COLUMN staged_restore")
            connection.execute("ALTER TABLE documents DROP COLUMN restore_batch")
            connection.execute("DROP TABLE restore_batches")
            connection.execute("PRAGMA user_version = 2")
        with patch.object(DocumentStore, "_schedule_cleanup"):
            reopened = DocumentStore(self.store.path)
        self.assertEqual(reopened.metadata(prepared["document_id"]), prepared)
        self.assertEqual(reopened.read_page(prepared["document_id"], 0), prepared["body"])
        self.assertEqual(reopened.total_characters(), prepared["body_characters"])
        with sqlite3.connect(self.store.path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
        new = reopened.prepare_text("A document after migrating the restore schema.")
        self.assertTrue(reopened.exists(new["document_id"]))
        with sqlite3.connect(self.store.path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 3)

    def test_staged_restore_does_not_relax_ordinary_document_capacity(self):
        with patch("documents.MAX_NOTEBOOK_CHARACTERS", 100):
            original = self.store.prepare_text("a" * 50)
            restore_id = self.store.begin_restore({original["document_id"]})
            staged = self.store.prepare_import({"text_file": self.source("b" * 80), "restore_id": restore_id})
            self.assertEqual(self.store.total_characters(), 50)
            self.assertTrue(self.store.exists(staged["document_id"]))
            with self.assertRaisesRegex(DocumentError, "capacity"):
                self.store.prepare_text("c" * 51)
            self.store.abort_restore(restore_id)
            self.assertTrue(self.store.exists(original["document_id"]))
            self.assertFalse(self.store.exists(staged["document_id"]))

    def test_many_matching_chunks_do_not_hide_other_documents(self):
        first = self.store.prepare_text("observatory " * 10_000)
        second = self.store.prepare_text("The observatory closes at night.")
        third = self.store.prepare_text("Visit the observatory before lunch.")
        self.assertEqual(self.store.matching_ids("observatory"),
                         {first["document_id"], second["document_id"], third["document_id"]})
        hits = self.store.search("observatory", limit=12)
        self.assertEqual({hit["document_id"] for hit in hits},
                         {first["document_id"], second["document_id"], third["document_id"]})
        self.assertLessEqual(sum(hit["document_id"] == first["document_id"] for hit in hits), 2)
        self.assertEqual(self.store.matching_ids("observatory", document_ids=[second["document_id"]]),
                         {second["document_id"]})

    def test_export_streams_verified_text_and_preserves_target_on_cancellation(self):
        text = "Exact export. 日本語\r\n" * 2000
        prepared = self.store.prepare_text(text)
        target = self.root / "backup" / "document.txt"
        self.assertEqual(self.store.export_document(prepared["document_id"], target), prepared)
        self.assertEqual(target.read_bytes(), text.encode("utf-8"))
        target.write_text("Keep my earlier backup", encoding="utf-8")
        calls = 0

        def cancel_later():
            nonlocal calls
            calls += 1
            return calls >= 3

        with self.assertRaises(DocumentCancelled):
            self.store.export_document(prepared["document_id"], target, cancel=cancel_later)
        self.assertEqual(target.read_text(encoding="utf-8"), "Keep my earlier backup")
        self.assertEqual(list(target.parent.glob("*.tmp")), [])

    def test_integrity_failure_does_not_overwrite_existing_backup(self):
        prepared = self.store.prepare_text("Original text " * 2000)
        with sqlite3.connect(self.store.path) as connection:
            connection.execute("UPDATE chunks SET text = replace(text, 'Original', 'Changed!') WHERE page = 1")
        target = self.root / "backup.txt"
        target.write_text("Earlier verified backup", encoding="utf-8")
        with self.assertRaisesRegex(DocumentError, "integrity"):
            self.store.export_document(prepared["document_id"], target)
        self.assertEqual(target.read_text(encoding="utf-8"), "Earlier verified backup")
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_opaque_ids_and_unrelated_database_are_rejected(self):
        for bad in ("../../notes", "/etc/passwd", "a" * 31, "A" * 32, 12):
            with self.assertRaises(DocumentError):
                self.store.metadata(bad)
        self.store.path.parent.mkdir(parents=True)
        with sqlite3.connect(self.store.path) as connection:
            connection.execute("CREATE TABLE important_data(value TEXT)")
            connection.execute("INSERT INTO important_data VALUES ('preserved')")
        with self.assertRaisesRegex(DocumentError, "unsupported"):
            self.store.prepare_text("Do not overwrite another database")
        with sqlite3.connect(self.store.path) as connection:
            self.assertEqual(connection.execute("SELECT value FROM important_data").fetchone()[0], "preserved")


if __name__ == "__main__":
    unittest.main()
