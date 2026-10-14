"""Shared notebook sync does not implicitly share employee data with Groq."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ai_brain import AutonomousBrain
from ai_chat import ChatWindow
from ai_privacy import filtered_ai_context, note_allowed_for_ai, notes_allowed_for_ai
from business_voice import BusinessVoice
from memory import MemoryStore
from notes import NoteService, NoteStore, current_guidance
from settings import AppSettings
from smart_notes import SmartNoteAssistant


app = QApplication.instance() or QApplication([])


class Credentials:
    def get(self):
        return "scripted-key"


class Client(QObject):
    completed = Signal(object)
    failed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.calls = []
        self.cancels = 0
        self.reply = None

    def send(self, model, messages, **kwargs):
        self.calls.append(messages)
        return True

    def cancel(self):
        self.cancels += 1


class Semantic(QObject):
    completed = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self):
        super().__init__()
        self.calls = []

    def recall(self, query, notes):
        self.calls.append((query, notes))
        return True

    def cancel(self):
        self.cancelled.emit()


class TeamAIPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = AppSettings(root / "settings.json")
        self.settings.values["team_share_notes_with_ai"] = False
        self.store = NoteStore(root / "notes.json")
        self.memory = MemoryStore(root / "memory.json")
        self.service = NoteService(self.store, self.settings)
        self.client = Client()
        self.semantic = Semantic()
        self.chat = ChatWindow(self.settings, Credentials(), self.context, lambda _: "ok",
            client=self.client, notebook_store=self.store, memory_store=self.memory,
            semantic_service=self.semantic)
        self.smart_client = Client()
        self.smart = SmartNoteAssistant(self.service, self.settings, Credentials(), client=self.smart_client)

    def tearDown(self):
        self.smart.stop()
        self.chat.shutdown()
        self.chat.close()
        self.service.stop()
        self.store.close()
        self.temp.cleanup()

    def note(self, shared=True):
        details = ({"team_id": "shared-note", "team_workspace_id": "work-office",
                    "team_revisions": ["revision-1"], "team_recipients": ["employee-secret-id"],
                    "team_due_at": 1900000000} if shared else {})
        note = self.store.add("Shared pickup" if shared else "Personal appointment",
                              "Confidential customer pickup at 14:00." if shared else "Personal appointment at 09:00.",
                              repeat=30, details=details)
        if shared:
            note["source"] = r"\\office-server\notes\private.txt"
        return note

    def context(self, metrics, notes):
        return {"incomplete_notes": self.store.notes,
                "team_inbox": [{"body": "Employee private message"}],
                "employees": [{"name": "Employee private roster"}],
                "misc": {"messages": [{"body": "Hidden nested inbox"}]}}

    def submit(self, text="What is scheduled?"):
        self.chat.input.setText(text)
        self.chat.submit()

    def test_enter_sends_one_message_without_opening_another_window(self):
        navigation = []
        for signal, label in ((self.chat.home_requested, "home"),
                              (self.chat.notepad_requested, "notepad"),
                              (self.chat.memory_requested, "memory")):
            signal.connect(lambda label=label: navigation.append(label))
        self.chat.tabs.setCurrentIndex(0)
        self.chat.show()
        self.chat.input.setText("Help me write a pickup message.")
        self.chat.input.setFocus()
        app.processEvents()
        QTest.keyClick(self.chat.input, Qt.Key_Return)
        app.processEvents()
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(self.client.calls[0][-1]["content"], "Help me write a pickup message.")
        self.assertEqual(navigation, [])
        self.assertTrue(self.chat.isVisible())
        self.assertEqual(self.chat.input.text(), "")

    def test_notebook_sync_keeps_cloud_opt_in_separate_from_personal_sharing(self):
        team = self.note()
        self.note(shared=False)
        self.memory.remember("like", "mint tea")
        self.submit()
        prompt = self.client.calls[-1][0]["content"]
        self.assertIn("Personal appointment", prompt)
        self.assertIn("mint tea", prompt)
        self.assertNotIn(team["body"], prompt)
        self.assertNotIn("Employee private", prompt)
        self.assertNotIn("Hidden nested inbox", prompt)

    def test_team_opt_in_sends_note_content_without_employee_metadata_or_network_path(self):
        team = self.note()
        self.settings.values["team_share_notes_with_ai"] = True
        self.submit()
        prompt = self.client.calls[-1][0]["content"]
        self.assertIn(team["body"], prompt)
        for private in ("work-office", "employee-secret-id", "revision-1", "office-server", "Employee private"):
            self.assertNotIn(private, prompt)

    def test_master_note_and_memory_sharing_still_apply_after_team_opt_in(self):
        self.note()
        self.memory.remember("like", "mint tea")
        self.settings.values.update(team_share_notes_with_ai=True, ai_share_notes=False, ai_share_memory=False)
        self.submit()
        prompt = self.client.calls[-1][0]["content"]
        self.assertNotIn("Confidential customer", prompt)
        self.assertNotIn("mint tea", prompt)

    def test_missing_team_setting_defaults_to_private_and_does_not_mutate_notes(self):
        note = self.note()
        self.settings.values.pop("team_share_notes_with_ai")
        self.assertFalse(note_allowed_for_ai(note, self.settings))
        self.settings.values["team_share_notes_with_ai"] = True
        allowed = notes_allowed_for_ai([note], self.settings)
        self.assertEqual(allowed[0]["source"], "")
        self.assertIn("office-server", note["source"])

    def test_partial_team_metadata_is_also_private_by_default(self):
        for marker in ({"team_revisions": ["revision-1"]}, {"team_recipients": ["employee-secret-id"]},
                       {"team_due_at": 1900000000}):
            with self.subTest(marker=marker):
                note = {"title": "Work note", "body": "Private work detail", **marker}
                self.assertFalse(note_allowed_for_ai(note, self.settings))
                self.assertEqual(filtered_ai_context({"notes": [note]}, self.settings)["notes"], [])

    def test_explicit_user_quote_remains_a_normal_chat_message(self):
        self.note()
        self.submit("Help me rewrite this message I pasted: Please call Customer A.")
        messages = self.client.calls[-1]
        self.assertIn("Please call Customer A", messages[-1]["content"])
        self.assertNotIn("Employee private message", messages[0]["content"])

    def test_local_semantic_query_excludes_unapproved_team_notes(self):
        self.settings.values["semantic_memory_enabled"] = True
        team, personal = self.note(), self.note(shared=False)
        self.submit()
        selected = self.semantic.calls[-1][1]
        self.assertEqual([row["id"] for row in selected], [personal["id"]])
        self.semantic.completed.emit({"query": self.chat.turn_query, "notes": [team, personal]})
        self.assertNotIn(team["body"], self.client.calls[-1][0]["content"])

    def test_semantic_completion_rechecks_team_sharing_permission(self):
        self.settings.values.update(semantic_memory_enabled=True, team_share_notes_with_ai=True)
        team = self.note()
        self.submit()
        self.settings.values["team_share_notes_with_ai"] = False
        self.semantic.completed.emit({"query": self.chat.turn_query, "notes": [team]})
        self.assertNotIn(team["body"], self.client.calls[-1][0]["content"])
        self.assertEqual(self.chat.semantic_notes, [])

    def test_withdrawal_clears_earlier_cloud_history_and_ignores_canceled_reply(self):
        self.settings.values["team_share_notes_with_ai"] = True
        self.note()
        self.submit()
        self.client.completed.emit({"content": "Confidential customer pickup at 14:00."})
        self.assertTrue(self.chat.history)
        self.submit("Tell me more")
        replies = []
        self.chat.reply_ready.connect(replies.append)
        self.settings.values["team_share_notes_with_ai"] = False
        self.chat.configure()
        self.client.completed.emit({"content": "A late confidential reply"})
        self.assertEqual(replies, [])
        self.assertEqual(self.chat.history, [])
        self.submit("Hello again")
        self.assertNotIn("Confidential customer", json.dumps(self.client.calls[-1]))

    def test_reminder_queue_and_manual_refresh_exclude_team_notes_by_default(self):
        team = self.note()
        self.smart.refresh()
        self.smart.request_reminder(team, "reminder")
        self.assertEqual(self.smart_client.calls, [])
        self.assertEqual(len(self.smart.queue), 0)
        self.assertIsNone(self.smart.pending)
        self.note(shared=False)
        self.smart.refresh()
        self.assertIn("Personal appointment", json.dumps(self.smart_client.calls[-1]))

    def test_opted_in_smart_reminder_respects_disabled_personal_memory(self):
        self.settings.values.update(team_share_notes_with_ai=True, ai_share_memory=False)
        team = self.note()
        calls = []
        self.smart.memory_context = lambda query: calls.append(query) or [{"text": "private personal preference"}]
        self.smart.request_reminder(team, "reminder")
        prompt = json.dumps(self.smart_client.calls[-1])
        self.assertIn(team["body"], prompt)
        self.assertNotIn("employee-secret-id", prompt)
        self.assertNotIn("office-server", prompt)
        self.assertEqual(calls, [])

    def test_pending_reminder_cannot_save_advice_after_team_permission_withdrawal(self):
        self.settings.values["team_share_notes_with_ai"] = True
        team = self.note()
        self.smart.refresh()
        self.settings.values["team_share_notes_with_ai"] = False
        self.smart_client.completed.emit({"content": json.dumps({"reminder": "Confidential reminder", "next_step": "Call customer",
                                                                "suggested_due": None, "reason": ""})})
        self.assertFalse(current_guidance(team))
        self.assertIsNone(self.smart.pending)
        self.assertFalse(self.smart.queue)

    def test_a_personal_note_shared_mid_request_no_longer_receives_automatic_advice(self):
        personal = self.note(shared=False)
        self.smart.refresh()
        self.service.modify(personal["id"], team_id="shared-note", team_workspace_id="work-office")
        self.smart_client.completed.emit({"content": json.dumps({"reminder": "Old reminder", "next_step": "Call",
                                                                "suggested_due": None, "reason": ""})})
        self.assertFalse(current_guidance(personal))
        self.assertEqual(len(self.smart_client.calls), 1)

    def test_background_prompts_remove_inboxes_and_unapproved_team_records(self):
        self.note()
        voice_client, brain_client = Client(), Client()
        voice = BusinessVoice(self.settings, Credentials(), self.context, lambda: self.store.notes,
                              lambda _: None, lambda: True, client=voice_client)
        brain = AutonomousBrain(self.settings, Credentials(), self.context, lambda: [], lambda _: None,
                                lambda _: None, lambda: True, client=brain_client)
        try:
            self.settings.values["ai_autonomy"] = True
            voice.request("startup")
            brain.request()
            for calls in (voice_client.calls, brain_client.calls):
                prompt = json.dumps(calls[-1])
                self.assertNotIn("Confidential customer", prompt)
                self.assertNotIn("Employee private", prompt)
                self.assertNotIn("Hidden nested inbox", prompt)
        finally:
            voice.stop()
            brain.stop()

    def test_background_replies_cannot_speak_shared_details_after_withdrawal(self):
        self.settings.values.update(team_share_notes_with_ai=True, ai_autonomy=True)
        self.note()
        spoken, actions = [], []
        voice_client, brain_client = Client(), Client()
        voice = BusinessVoice(self.settings, Credentials(), self.context, lambda: self.store.notes,
                              spoken.append, lambda: True, client=voice_client)
        brain = AutonomousBrain(self.settings, Credentials(), self.context, lambda: [], actions.append,
                                spoken.append, lambda: True, client=brain_client)
        try:
            voice.request("startup")
            brain.request()
            self.settings.values["team_share_notes_with_ai"] = False
            voice_client.completed.emit({"content": "Confidential customer"})
            brain_client.completed.emit({"content": json.dumps({"action": "animate", "animation": "IDLE",
                                                                "target_id": "", "say": "Confidential customer"})})
            self.assertEqual(spoken, [])
            self.assertEqual(actions, [])
        finally:
            voice.stop()
            brain.stop()

    def test_local_business_greetings_do_not_become_automatic_cloud_context(self):
        available, spoken = [False], []
        client = Client()
        voice = BusinessVoice(self.settings, Credentials(), self.context, lambda: self.store.notes,
                              spoken.append, lambda: available[0], client=client)
        try:
            with patch.object(voice, "local", return_value="A private workplace order count"):
                voice.request("startup")
            self.assertIn("A private workplace order count", spoken)
            available[0] = True
            voice.request("interaction")
            self.assertNotIn("A private workplace order count", json.dumps(client.calls[-1]))
        finally:
            voice.stop()

    def test_nested_context_is_filtered_without_mutating_transport_state(self):
        team = self.note()
        context = self.context(False, True)
        sanitized = filtered_ai_context(context, self.settings)
        self.assertEqual(sanitized["incomplete_notes"], [])
        self.assertIn("team_inbox", context)
        self.assertIn(team, context["incomplete_notes"])
        self.assertNotIn("team_inbox", sanitized)


if __name__ == "__main__":
    unittest.main()
