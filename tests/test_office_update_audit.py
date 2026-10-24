"""Independent checks for inventory sharing and withdrawn cloud consent."""
import json
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from ai_chat import ChatWindow
from ai_privacy import filtered_ai_context
from settings import AppSettings


APP = QApplication.instance() or QApplication([])


class ScriptedClient(QObject):
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


class Credentials:
    def get(self):
        return "scripted-key"


class InventoryCloudConsentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = AppSettings(Path(self.temp.name) / "settings.json")
        self.client = ScriptedClient()
        self.inventory_calls = []
        self.stock = {"items": [{"sku": "FILTER-007", "description": "Cabin air filter",
                                 "quantity": 2, "location": "Shelf C-4",
                                 "reorder_point": 3}],
                      "source": "Shared office inventory", "status": "Offline cached stock",
                      "last_import_at": "2026-10-14T10:00:00Z", "live_stock_count": False}
        self.chat = ChatWindow(self.settings, Credentials(), self.context,
                               lambda action: "ok", client=self.client)
        self.chat.inventory_context = self.inventory_context

    def tearDown(self):
        self.chat.shutdown()
        self.chat.close()
        self.temp.cleanup()

    def context(self, metrics, notes):
        # The final prompt filter also protects earlier context producers.
        return {"nested": {"inventory_references": self.stock},
                "inventory_summary": {"private_stock_marker": "FILTER-007"},
                "messages": [{"body": "PRIVATE EMPLOYEE MESSAGE"}]}

    def inventory_context(self, query):
        self.inventory_calls.append(query)
        return self.stock

    def test_notepad_stock_lookup_respects_note_and_team_sharing(self):
        from types import SimpleNamespace
        self.settings.values.update(inventory_share_with_ai=True, ai_share_notes=True, team_share_notes_with_ai=False)
        self.chat.notes.setChecked(True)
        self.chat.notebook_store = SimpleNamespace(notes=[{'id': 'secret', 'title': 'Team order', 'team_shared': True}])
        calls = []
        self.chat.inventory_notepad_context = lambda notes: calls.append(notes) or {'requests': []}
        self.chat.system_message()
        self.assertEqual(calls, [[]])
        self.settings.values['team_share_notes_with_ai'] = True
        self.chat.system_message()
        self.assertEqual(calls[-1][0]['id'], 'secret')
        self.settings.values['inventory_share_with_ai'] = False
        previous = len(calls)
        self.chat.system_message()
        self.assertEqual(len(calls), previous)

    def submit(self, text="Where is FILTER-007, and does it need restocking?"):
        self.chat.input.setText(text)
        self.chat.submit()
        self.assertTrue(self.client.calls)
        return self.client.calls[-1][0]["content"]

    def test_inventory_is_excluded_by_default_even_with_personal_notes_enabled(self):
        self.assertTrue(self.settings["ai_share_notes"])
        prompt = self.submit()
        self.assertNotIn("FILTER-007", prompt)
        self.assertNotIn("Shelf C-4", prompt)
        self.assertNotIn("PRIVATE EMPLOYEE MESSAGE", prompt)
        self.assertEqual(self.inventory_calls, [])

    def test_inventory_opt_in_is_independent_and_preserves_snapshot_caveats(self):
        self.settings.values.update(inventory_share_with_ai=True, ai_share_notes=False,
                                    ai_share_memory=False, team_share_notes_with_ai=False)
        self.chat.notes.setChecked(False)
        self.chat.configure()
        prompt = self.submit()
        for text in ("FILTER-007", "Shelf C-4", "Offline cached stock", "2026-10-14T10:00:00Z"):
            self.assertIn(text, prompt)
        self.assertIn('"live_stock_count": false', prompt)
        self.assertNotIn("PRIVATE EMPLOYEE MESSAGE", prompt)
        self.assertEqual(len(self.inventory_calls), 1)

    def test_inventory_consent_withdrawal_cancels_reply_and_clears_cloud_history(self):
        self.settings.values["inventory_share_with_ai"] = True
        self.chat.configure()
        self.submit()
        self.client.completed.emit({"content": "FILTER-007 is on Shelf C-4."})
        self.assertTrue(self.chat.history)
        self.submit("How much should we buy?")
        replies = []
        self.chat.reply_ready.connect(replies.append)
        cancellations = self.client.cancels
        self.settings.values["inventory_share_with_ai"] = False
        self.chat.configure()
        self.client.completed.emit({"content": "Late private stock response"})
        self.assertGreater(self.client.cancels, cancellations)
        self.assertEqual(replies, [])
        self.assertEqual(self.chat.history, [])
        self.submit("Hello")
        self.assertNotIn("FILTER-007", json.dumps(self.client.calls[-1]))
        self.assertNotIn("Shelf C-4", json.dumps(self.client.calls[-1]))

    def test_final_context_filter_removes_nested_inventory_when_consent_is_off(self):
        filtered = filtered_ai_context({"nested": {"inventory": self.stock,
                                                   "inventory_summary": self.stock,
                                                   "inventory_references": self.stock},
                                        "healthy": "keep"}, self.settings, share_notes=False)
        self.assertEqual(filtered, {"nested": {}, "healthy": "keep"})


if __name__ == "__main__":
    unittest.main()
