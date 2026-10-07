"""Disk-backed documents reach chat through bounded, current excerpts."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from ai_chat import ChatWindow
from memory import MemoryStore
from notes import NoteStore
from settings import AppSettings


app = QApplication.instance() or QApplication([])


class ScriptedClient(QObject):
    completed = Signal(object)
    failed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.reply = None
        self.calls = []

    def send(self, model, messages, tools=True, json_mode=False):
        self.calls.append(messages)
        return True

    def cancel(self):
        self.reply = None


class ScaledRecallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = AppSettings(root / "settings.json")
        self.memory = MemoryStore(root / "memory.json")
        self.store = NoteStore(root / "notes.json")
        self.client = ScriptedClient()
        self.chat = ChatWindow(self.settings, SimpleNamespace(get=lambda: "scripted-key"),
            lambda metrics, notes: {}, lambda action: "ok", client=self.client,
            memory_store=self.memory, notebook_store=self.store)

    def tearDown(self):
        self.chat.shutdown()
        self.chat.close()
        self.store.close()
        self.temp.cleanup()

    def save_large_document(self):
        # The fact is deliberately well beyond the note's 10,000-character
        # preview, so returning its preview alone cannot pass this test.
        text = "Guide introduction and background material. " * 2800
        text += "\nThe observatory opens at 18:30 and its entrance is the north gate.\n"
        prepared = self.store.documents.prepare_text(text, title="Travel guide",
            other_note_characters=self.store.inline_character_count())
        return self.store.add_prepared_document(prepared, "Travel guide", "guide.pdf")

    def context_payload(self):
        message = self.chat.system_message()["content"]
        return message, json.loads(message.split("Current app context: ", 1)[1])

    def test_large_completed_document_fact_is_recalled_after_restart(self):
        note = self.save_large_document()
        self.assertLessEqual(len(note["body"]), 10000)
        self.assertNotIn("18:30", note["body"])
        self.store.complete(note["id"])
        restored = NoteStore(self.store.path)
        self.chat.notebook_store = restored
        self.chat.turn_query = "When does the observatory open and where is its entrance?"
        message, context = self.context_payload()
        self.assertIn("18:30", message)
        self.assertIn("north gate", message)
        reference = next(row for row in context["notebook_references"] if row["id"] == note["id"])
        self.assertTrue(reference["done"])
        self.assertEqual(reference["document_source"], "guide.pdf")
        self.assertLess(len(message), 14000)

    def test_sharing_off_does_not_read_the_document_index(self):
        self.save_large_document()
        self.chat.notes.setChecked(False)
        self.settings.values["ai_share_memory"] = False
        self.chat.turn_query = "When does the observatory open?"
        with patch.object(self.store, "recall_notes", side_effect=AssertionError("Private document queried")):
            message, context = self.context_payload()
        self.assertNotIn("notebook_references", context)
        self.assertNotIn("18:30", message)

    def test_legacy_notebook_stores_still_work(self):
        self.chat.notebook_store = SimpleNamespace(notes=[{
            "id": "legacy", "title": "Museum", "body": "The museum opens at 09:15.",
            "done": False, "updated": 100}])
        self.chat.turn_query = "When does the museum open?"
        message, context = self.context_payload()
        self.assertIn("09:15", message)
        self.assertEqual(context["notebook_references"][0]["id"], "legacy")

    def test_app_context_keeps_large_document_as_a_short_preview(self):
        from main import BuddyApp
        note = self.save_large_document()
        coordinator = SimpleNamespace(settings=self.settings, notes_store=self.store,
            memory_store=self.memory, pet=SimpleNamespace(hidden_in=None),
            focus=SimpleNamespace(clock=SimpleNamespace(state="idle")),
            desktop=SimpleNamespace(bridge=SimpleNamespace(targets=[], supported=False)),
            latest_snapshot=None)
        context = BuddyApp.ai_context(coordinator, False, True)
        reference = next(row for row in context["incomplete_notes"] if row["title"] == note["title"])
        self.assertLessEqual(len(reference["body"]), 1000)
        self.assertEqual(reference["document_source"], "guide.pdf")
        self.assertGreater(reference["document_characters"], 100000)
        self.assertLess(len(json.dumps(context)), 8000)

    def test_large_web_source_spools_are_released_on_replacement_and_shutdown(self):
        from content_intake import _result
        first = _result("web", "First source", "Reference text. " * 9000,
            "https://example.org/first")
        first_path = Path(first["text_file"])
        self.chat.web_received(first)
        self.assertTrue(first_path.exists())
        second = _result("web", "Second source", "Other reference. " * 9000,
            "https://example.org/second")
        second_path = Path(second["text_file"])
        self.chat.web_received(second)
        self.assertFalse(first_path.exists())
        self.assertTrue(second_path.exists())
        self.chat.shutdown()
        self.assertFalse(second_path.exists())

    def test_web_failure_releases_the_previous_full_source(self):
        from content_intake import _result
        result = _result("web", "Source", "Reference text. " * 9000,
            "https://example.org/source")
        path = Path(result["text_file"])
        self.chat.web_received(result)
        self.chat.web_failed("Source unavailable")
        self.assertFalse(path.exists())
        self.assertIsNone(self.chat.web_data)


if __name__ == "__main__":
    unittest.main()
