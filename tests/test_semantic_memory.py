"""Bounded local semantic recall, cache invalidation and optional chat integration."""
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from ai_chat import ChatWindow
from documents import DocumentError, DocumentStore
from memory import MemoryStore
from notes import NoteStore
from semantic_memory import INDEX_BATCH, MAX_CANDIDATES, SemanticError, SemanticMemory, SemanticService
from settings import AppSettings


app = QApplication.instance() or QApplication([])


class Encoder:
    def __init__(self):
        self.calls = []
        self.started = None
        self.wait = None

    def encode(self, texts, **kwargs):
        self.calls.append(list(texts))
        if self.started is not None:
            self.started.set()
            self.wait.wait(timeout=3)
        result = []
        for text in texts:
            text = text.casefold()
            index = 0 if any(word in text for word in ("medical", "physician", "doctor")) else 1 if "football" in text else 2
            vector = [0.0] * 32
            vector[index] = 1.0
            result.append(vector)
        return result


class Client(QObject):
    completed = Signal(object)
    failed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.calls = []
        self.reply = None

    def send(self, model, messages, **kwargs):
        self.calls.append(messages)
        return True

    def cancel(self):
        pass


class ScriptedSemantic(QObject):
    completed = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self):
        super().__init__()
        self.calls = []
        self.canceled = False

    def recall(self, query, notes):
        self.calls.append((query, notes))
        return True

    def cancel(self):
        self.canceled = True
        self.cancelled.emit()


class SemanticMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.model = self.root / "model"
        self.model.mkdir()
        self.encoder = Encoder()
        self.documents = DocumentStore(self.root / "documents.sqlite")
        self.index = SemanticMemory(self.root / "semantic.sqlite", self.model,
            documents=self.documents, encoder_factory=lambda path: self.encoder)

    def tearDown(self):
        self.documents.close()
        self.temp.cleanup()

    def note(self, identifier="appointment", body="Physician consultation on Tuesday at 09:30."):
        return {"id": identifier, "title": "Appointment", "body": body, "done": False, "updated": 1}

    def test_synonyms_are_recalled_and_cache_persists_without_reembedding(self):
        note = self.note()
        result = self.index.recall({"query": "When is my medical visit?", "notes": [note]})
        self.assertEqual(result["notes"][0]["id"], note["id"])
        self.assertIn("09:30", result["notes"][0]["body"])
        replacement = Encoder()
        restarted = SemanticMemory(self.index.path, self.model, encoder_factory=lambda path: replacement)
        restarted.recall({"query": "medical visit", "notes": [note]})
        self.assertEqual(replacement.calls, [["medical visit"]])

    def test_each_incremental_index_job_has_a_fixed_embedding_budget(self):
        notes = [self.note(str(number), "Physician " + "text " * 1800) for number in range(20)]
        self.index.refresh({"notes": notes})
        self.assertLessEqual(sum(len(batch) for batch in self.encoder.calls), INDEX_BATCH)
        self.assertTrue(all(len(batch) <= 8 for batch in self.encoder.calls))
        self.assertTrue(all(len(text) <= 1001 for batch in self.encoder.calls for text in batch))
        self.assertIn("continues", self.index.refresh({"notes": notes})["status"])

    def test_changed_and_deleted_notes_cannot_use_stale_embeddings(self):
        note = self.note()
        self.index.recall({"query": "medical visit", "notes": [note]})
        edited = {**note, "body": "Football training on Friday."}
        self.assertEqual(self.index.recall({"query": "medical visit", "notes": [edited]})["notes"], [])
        self.assertEqual(self.index.recall({"query": "medical visit", "notes": []})["notes"], [])
        self.assertEqual(note["body"], "Physician consultation on Tuesday at 09:30.")

    def test_model_switch_invalidates_and_reclaims_the_previous_model_cache(self):
        self.index.refresh({"notes": [self.note()]})
        replacement = self.root / "replacement-model"
        replacement.mkdir()
        self.index.configure(replacement)
        self.index.refresh({"notes": []})
        with sqlite3.connect(self.index.path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sources WHERE active = 1").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM vectors").fetchone()[0], 0)

    def test_large_document_progress_reaches_content_beyond_the_preview(self):
        text = "Reference background. " * 1200 + "\nPhysician consultation starts at 10:45."
        prepared = self.documents.prepare_text(text, title="Schedule")
        note = {**self.note(), **prepared, "body": prepared["body"]}
        for _ in range(8):
            self.index.refresh({"notes": [note]})
        result = self.index.recall({"query": "medical visit", "notes": [note]})
        self.assertIn("10:45", result["notes"][0]["body"])
        self.assertLessEqual(len(result["notes"][0]["body"]), 1800)

    def test_missing_local_model_never_calls_an_encoder_or_creates_a_cache(self):
        called = []
        index = SemanticMemory(self.root / "missing.sqlite", self.root / "absent-model",
                               encoder_factory=lambda path: called.append(path))
        with self.assertRaisesRegex(SemanticError, "No model is downloaded"):
            index.refresh({"notes": [self.note()]})
        self.assertEqual(called, [])
        self.assertFalse(index.path.exists())

    def test_bad_embedding_keeps_notebook_and_returns_a_safe_error(self):
        with patch.object(self.encoder, "encode", return_value=[[float("nan")]]):
            with self.assertRaisesRegex(SemanticError, "invalid embedding"):
                self.index.refresh({"notes": [self.note()]})
        self.assertEqual(self.note()["body"], "Physician consultation on Tuesday at 09:30.")

    def test_query_candidates_are_bounded_for_a_large_cached_corpus(self):
        notes = [self.note(str(number)) for number in range(700)]
        for _ in range(22):
            self.index.refresh({"notes": notes})
        checked = []
        import semantic_memory
        original = semantic_memory.struct.unpack
        with patch.object(semantic_memory.struct, "unpack", side_effect=lambda *args: checked.append(1) or original(*args)):
            self.index.recall({"query": "medical visit", "notes": notes})
        self.assertLessEqual(len(checked), MAX_CANDIDATES)
        self.assertGreater(len(checked), 0)

    def test_hot_bucket_query_uses_index_order_and_bounds_sqlite_work(self):
        note = self.note()
        self.index.refresh({"notes": [note]})
        model = self.index._model_identity
        with self.index._connection(None) as connection:
            # Thousands of identical vectors exercise a crowded lookup bucket;
            # SQL LIMIT must bound database work, not just Python dot products.
            connection.execute("""WITH RECURSIVE counter(n) AS (
                SELECT 1 UNION ALL SELECT n+1 FROM counter WHERE n < 20000
                ) INSERT INTO vectors SELECT vectors.model, note_id, fingerprint,
                page, offset + counter.n, text, dimensions, embedding,
                bucket0, bucket1, bucket2, bucket3 FROM vectors
                CROSS JOIN counter WHERE vectors.rowid = 1""")
            buckets = self.index._buckets(self.index._encode(["medical visit"], None)[0])
            for table, bucket in enumerate(buckets):
                plan = connection.execute("EXPLAIN QUERY PLAN " + self.index._candidate_sql(table), (model, bucket, 32)).fetchall()
                self.assertFalse(any("TEMP B-TREE" in row["detail"] for row in plan))
            steps = []
            connection.set_progress_handler(lambda: steps.append(1) or 0, 100)
            found = self.index._search(connection, model, "medical visit", [note], None)
            self.assertTrue(found)
            self.assertLess(len(steps) * 100, 30000)

    def test_canceled_worker_does_not_deliver_stale_result_or_block_qt(self):
        service = SemanticService(self.index)
        results = []
        service.completed.connect(results.append)
        self.encoder.started, self.encoder.wait = threading.Event(), threading.Event()
        try:
            self.assertTrue(service.recall("medical visit", [self.note()]))
            self.assertTrue(self.encoder.started.wait(timeout=2))
            begin = time.monotonic()
            service.cancel()
            self.assertLess(time.monotonic() - begin, 0.2)
            self.encoder.wait.set()
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.005)
            self.assertEqual(results, [])
        finally:
            self.encoder.wait.set()
            service.shutdown()

    def test_reapplying_unchanged_model_setting_preserves_active_recall(self):
        service = SemanticService(self.index)
        self.encoder.started, self.encoder.wait = threading.Event(), threading.Event()
        try:
            self.assertTrue(service.recall("medical visit", [self.note()]))
            self.assertTrue(self.encoder.started.wait(timeout=2))
            service.configure(str(self.model))
            self.assertTrue(service.busy)
        finally:
            self.encoder.wait.set()
            service.shutdown()
            deadline = time.monotonic() + 0.1
            while time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.005)


class SemanticChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = AppSettings(root / "settings.json")
        self.memory = MemoryStore(root / "memory.json")
        self.notebook = NoteStore(root / "notes.json")
        self.client = Client()
        self.semantic = ScriptedSemantic()
        self.chat = ChatWindow(self.settings, type("Credentials", (), {"get": lambda self: "scripted-key"})(),
            lambda metrics, notes: {}, lambda action: "ok", client=self.client,
            memory_store=self.memory, notebook_store=self.notebook, semantic_service=self.semantic)

    def tearDown(self):
        self.chat.shutdown()
        self.chat.close()
        self.notebook.close()
        self.temp.cleanup()

    def submit(self):
        self.chat.input.setText("When is my medical appointment?")
        self.chat.submit()

    def test_default_does_not_start_semantic_work(self):
        self.submit()
        self.assertEqual(self.semantic.calls, [])
        self.assertEqual(len(self.client.calls), 1)

    def test_meaning_lookup_waits_offline_then_adds_bounded_reference(self):
        self.settings.values["semantic_memory_enabled"] = True
        note = self.notebook.add("Appointment", "Physician consultation Tuesday at 09:30.", repeat=0)
        self.submit()
        self.assertEqual(self.client.calls, [])
        self.assertFalse(self.chat.send_button.isEnabled())
        self.semantic.completed.emit({"query": self.chat.turn_query, "notes": [note]})
        content = self.client.calls[-1][0]["content"]
        context = json.loads(content.split("Current app context: ", 1)[1])
        self.assertIn("semantic_notebook_references", context)
        self.assertIn("09:30", content)
        self.assertEqual(self.notebook.notes[0]["body"], note["body"])

    def test_sharing_off_avoids_semantic_query_and_withdrawal_drops_pending_data(self):
        self.settings.values["semantic_memory_enabled"] = True
        note = self.notebook.add("Private", "Physician information", repeat=0)
        self.chat.notes.setChecked(False)
        self.submit()
        self.assertEqual(self.semantic.calls, [])
        self.chat.notes.setChecked(True)
        self.submit()
        self.chat.notes.setChecked(False)
        self.semantic.completed.emit({"query": self.chat.turn_query, "notes": [note]})
        self.assertNotIn("Physician information", self.client.calls[-1][0]["content"])

    def test_deleted_note_and_canceled_query_are_not_sent(self):
        self.settings.values["semantic_memory_enabled"] = True
        note = self.notebook.add("Appointment", "Physician consultation Tuesday.", repeat=0)
        self.submit()
        self.notebook.delete(note["id"])
        self.semantic.completed.emit({"query": self.chat.turn_query, "notes": [note]})
        self.assertNotIn("Physician consultation", self.client.calls[-1][0]["content"])
        self.submit()
        count = len(self.client.calls)
        self.chat.cancel_request()
        self.semantic.completed.emit({"query": self.chat.turn_query, "notes": [note]})
        self.assertEqual(len(self.client.calls), count)
        self.assertTrue(self.chat.send_button.isEnabled())
        self.assertIn("medical appointment", self.chat.input.text())

    def test_semantic_failure_falls_back_to_keyword_chat(self):
        self.settings.values["semantic_memory_enabled"] = True
        self.submit()
        self.semantic.failed.emit("Model unavailable")
        self.assertEqual(len(self.client.calls), 1)
        self.assertTrue(self.chat.send_button.isEnabled())

    def test_privacy_withdrawal_cancels_pending_lookup_and_restores_message(self):
        self.settings.values["semantic_memory_enabled"] = True
        note = self.notebook.add("Appointment", "Private physician details", repeat=0)
        self.submit()
        self.chat.preferences_changed.connect(lambda change: self.semantic.cancel() if not change["ai_share_notes"] else None)
        self.chat.notes.setChecked(False)
        self.assertFalse(self.chat.semantic_loading)
        self.assertTrue(self.chat.send_button.isEnabled())
        self.assertIn("medical appointment", self.chat.input.text())
        self.semantic.completed.emit({"query": self.chat.turn_query, "notes": [note]})
        self.assertEqual(self.client.calls, [])

    def test_edited_inline_note_drops_old_semantic_fact(self):
        self.settings.values["semantic_memory_enabled"] = True
        note = self.notebook.add("Appointment", "Physician at 09:30.", repeat=0)
        self.submit()
        stale = dict(note)
        self.notebook.find(note["id"])["body"] = "Physician at 11:30."
        self.semantic.completed.emit({"query": self.chat.turn_query, "notes": [stale]})
        self.assertNotIn("09:30", self.client.calls[-1][0]["content"])

    def test_appended_correction_drops_the_old_semantic_excerpt(self):
        self.settings.values["semantic_memory_enabled"] = True
        note = self.notebook.add("Appointment", "Physician visit Tuesday at 09:30.", repeat=0)
        self.submit()
        stale = dict(note)
        self.notebook.find(note["id"])["body"] += "\nCanceled; the appointment is now Wednesday at 11:30."
        self.semantic.completed.emit({"query": self.chat.turn_query, "notes": [stale]})
        content = self.client.calls[-1][0]["content"]
        context = json.loads(content.split("Current app context: ", 1)[1])
        self.assertNotIn("semantic_notebook_references", context)
        self.assertIn("now Wednesday", content)

    def test_missing_local_model_falls_back_asynchronously_without_download(self):
        self.settings.values["semantic_memory_enabled"] = True
        root = Path(self.temp.name)
        actual = SemanticService(SemanticMemory(root / "semantic.sqlite", root / "absent-model"))
        self.chat.semantic_service = actual
        actual.completed.connect(self.chat.semantic_received)
        actual.failed.connect(self.chat.semantic_failed)
        actual.cancelled.connect(self.chat.semantic_cancelled)
        try:
            self.submit()
            deadline = time.monotonic() + 1
            while not self.client.calls and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.005)
            self.assertEqual(len(self.client.calls), 1)
            self.assertFalse(self.chat.semantic_loading)
            self.assertFalse((root / "semantic.sqlite").exists())
        finally:
            actual.shutdown()

    def test_unknown_slash_command_does_not_execute_or_reach_groq(self):
        self.chat.input.setText("/waev")
        self.chat.submit()
        self.assertEqual(self.client.calls, [])
        self.assertIn("Unknown play command", self.chat.status.text())

    def test_busy_or_damaged_keyword_index_falls_back_to_saved_previews(self):
        self.notebook.add("Museum", "The museum opens at 09:15.", repeat=0)
        with patch.object(self.notebook, "recall_notes", side_effect=DocumentError("Notebook storage is busy.")):
            self.submit()
        self.assertEqual(len(self.client.calls), 1)
        content = self.client.calls[-1][0]["content"]
        self.assertIn("09:15", content)
        self.assertIn("notebook_recall_status", content)
        self.assertTrue(self.chat.send_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
