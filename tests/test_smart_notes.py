"""Personalized reminder persistence, stale replies, scheduling, and offline delivery."""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication
from PySide6.QtNetwork import QNetworkRequest
from unittest.mock import patch
from ai_chat import GroqClient
from notes import NoteService, NoteStore, current_guidance
from settings import AppSettings
from smart_notes import SmartNoteAssistant, parse_guidance, note_prompt


class Credentials:
    key = "placeholder-session"
    def get(self): return self.key


class Client(QObject):
    completed = Signal(object)
    failed = Signal(str)
    def __init__(self):
        super().__init__()
        self.calls = []
        self.cancels = 0
    def send(self, model, messages, tools=True, json_mode=False):
        self.calls.append((model, messages, tools, json_mode))
        return True
    def cancel(self): self.cancels += 1


def reply(due=None):
    return json.dumps({"reminder": "Prepare the order before pickup.",
        "next_step": "Check the stock and pack the items.", "suggested_due": due,
        "reason": "Use the pickup time written in your note." if due else ""})


class SmartNotesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = NoteStore(root / "notes.json")
        self.settings = AppSettings(root / "settings.json")
        self.service = NoteService(self.store, self.settings)
        self.client = Client()
        self.credentials = Credentials()
        self.assistant = SmartNoteAssistant(self.service, self.settings, self.credentials, client=self.client)

    def tearDown(self):
        self.assistant.stop()
        self.service.stop()
        self.temp.cleanup()

    def note(self, body="Pack the order before pickup.", due=None):
        return self.service.save_note(None, "Customer order", body, 30, due)

    def test_saved_note_is_sent_as_data_without_source_path_or_tools(self):
        note = self.note()
        self.service.modify(note["id"], source="C:/Private/JefferyNotes.txt")
        self.assistant.refresh()
        _, messages, tools, mode = self.client.calls[-1]
        self.assertFalse(tools)
        self.assertTrue(mode)
        payload = json.loads(messages[1]["content"])
        self.assertEqual(payload["body"], note["body"])
        self.assertTrue(payload["from_linked_notepad"])
        self.assertNotIn("C:/Private", json.dumps(messages))
        self.assertIn("local_now", payload)

    def test_advice_is_persisted_without_changing_user_schedule(self):
        due = time.time() + 900
        note = self.note(due=due)
        self.assistant.refresh()
        self.client.completed.emit({"content": reply("2035-01-01T09:00:00-05:00")})
        stored = NoteStore(self.store.path).find(note["id"])
        self.assertEqual(stored["next_due"], due)
        self.assertEqual(current_guidance(stored)["reminder"], "Prepare the order before pickup.")
        self.assertIsNone(current_guidance(stored)["suggested_due"], "A far-off guess survived")
        self.assistant.refresh()
        self.assertEqual(len(self.client.calls), 1, "Unchanged notes should use saved advice")

    def test_changed_note_rejects_old_reply_and_refreshes(self):
        note = self.note()
        self.assistant.refresh()
        self.service.save_note(note["id"], "Customer order", "Cancel pickup; call the customer first.", 30)
        self.client.completed.emit({"content": reply()})
        self.assertFalse(current_guidance(self.store.find(note["id"])))
        self.assertEqual(len(self.client.calls), 2)
        self.assertIn("Cancel pickup", self.client.calls[-1][1][1]["content"])

    def test_completed_and_deleted_notes_cannot_receive_advice(self):
        note = self.note()
        self.assistant.refresh()
        self.service.complete(note["id"])
        self.client.completed.emit({"content": reply()})
        self.assertFalse(current_guidance(self.store.find(note["id"])))
        self.assertFalse(self.assistant.queue)
        other = self.note()
        self.assistant.refresh()
        self.service.delete(other["id"])
        self.client.completed.emit({"content": reply()})
        self.assertIsNone(self.store.find(other["id"]))

    def test_missing_or_rejected_key_keeps_local_notification_working(self):
        self.credentials.key = ""
        note = self.note()
        self.assistant.refresh()
        self.assertFalse(self.client.calls)
        delivered = []
        self.service.notification.connect(lambda row, kind: delivered.append((row["id"], kind)))
        self.service.tick()
        self.assertEqual(delivered, [(note["id"], "added")])
        self.credentials.key = "placeholder-session"
        self.assistant.reset()
        self.client.failed.emit("Groq rejected the key. Save a working key in Connection.")
        self.assertTrue(self.assistant.blocked)
        count = len(self.client.calls)
        self.assistant.pump()
        self.assertEqual(len(self.client.calls), count)
        self.assistant.reset()
        self.assertFalse(self.assistant.blocked)
        self.assertEqual(len(self.client.calls), count + 1)

    def test_sharing_off_cancels_pending_and_ignores_reply(self):
        note = self.note()
        self.assistant.refresh()
        self.settings.values["ai_share_notes"] = False
        self.assistant.configure()
        self.client.completed.emit({"content": reply()})
        self.assertIsNone(self.assistant.pending)
        self.assertFalse(self.assistant.queue)
        self.assertFalse(current_guidance(note))

    def test_malformed_reply_backs_off_and_does_not_touch_note(self):
        note = self.note()
        self.assistant.refresh()
        due = note["next_due"]
        self.client.completed.emit({"content": '{"reminder":"Do this","action":"delete"}'})
        self.assertFalse(current_guidance(note))
        self.assertEqual(note["next_due"], due)
        self.assertGreater(self.assistant.next_request, time.monotonic() + 50)

    def test_cached_advice_invalidates_on_edit_but_survives_snooze(self):
        note = self.note()
        self.service.modify(note["id"], ai_guidance=parse_guidance(reply(), note))
        self.service.snooze(note["id"], 15)
        self.assertTrue(current_guidance(note))
        self.service.save_note(note["id"], "Customer order", "New pickup details", 30)
        self.assertFalse(current_guidance(note))

    def test_offset_dates_validate_and_ambiguous_dates_are_not_used(self):
        note = self.note()
        now = 1791349200
        valid = parse_guidance(reply("2026-10-08T09:00:00-05:00"), note, now)
        self.assertGreater(valid["suggested_due"], now)
        for due in ("2026-10-08T09:00:00", "2020-01-01T09:00:00+00:00", "tomorrow", ""):
            self.assertIsNone(parse_guidance(reply(due), note, now)["suggested_due"])

    def test_relative_dates_keep_the_time_the_note_was_written(self):
        note = self.store.add('Call', 'Call tomorrow at 9 AM', now=100)
        self.store.edit(note['id'], 'Call', 'Call tomorrow at 9 AM', 30, now=200)
        self.assertEqual(note['written'], 100, 'Only the schedule changed')
        self.store.edit(note['id'], 'Call', 'Call tomorrow at 10 AM', 30, now=300)
        self.assertEqual(note['written'], 300)
        payload = json.loads(note_prompt(note, now=90000)[1]['content'])
        self.assertNotEqual(payload['note_saved_at'], payload['local_now'])
        self.service.snooze(note['id'], 30)
        self.assertEqual(note['written'], 300, 'Snoozing must not move a relative deadline')

    def test_linked_lines_are_available_to_the_reminder_assistant(self):
        linked = Path(self.temp.name) / "JefferyNotes.txt"
        linked.write_text("Call the customer tomorrow at 9 AM\n", encoding="utf-8")
        self.service.link(linked)
        self.assistant.refresh()
        self.assertIn("Call the customer tomorrow at 9 AM", self.client.calls[-1][1][1]["content"])
        self.assertNotIn(str(linked), self.client.calls[-1][1][1]["content"])

    def test_late_canceled_reply_does_not_consume_new_request(self):
        class Reply(QObject):
            finished = Signal()
            def abort(self): pass  # The old completion is deliberately delayed.
            def attribute(self, key): return 200
            def readAll(self): return json.dumps({'choices': [{'message': {'content': 'Current reply'}}]}).encode()
        transport = GroqClient(self.credentials)
        old, new = Reply(), Reply()
        completed = []
        transport.completed.connect(completed.append)
        with patch.object(transport.manager, 'post', side_effect=[old, new]) as post:
            transport.send('openai/gpt-oss-20b', note_prompt(self.note()), tools=False, json_mode=True)
            payload = json.loads(bytes(post.call_args.args[1]))
            self.assertEqual(payload['response_format'], {'type': 'json_object'})
            self.assertNotIn('tools', payload)
            transport.cancel()
            transport.send('openai/gpt-oss-20b', [{'role': 'user', 'content': 'New request'}], tools=False)
            old.finished.emit()
            self.assertIs(transport.reply, new)
            self.assertFalse(completed)
            new.finished.emit()
            self.assertEqual(completed[0]['content'], 'Current reply')
        transport.cancel()


if __name__ == "__main__":
    unittest.main()
