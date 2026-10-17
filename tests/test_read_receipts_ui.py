"""Read receipts require a foreground conversation at its newest message."""
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog
from settings import AppSettings
from work_chat import WorkChatWindow
from test_work_chat import APP, ScriptedApi, message


class ReadReceiptUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.window = WorkChatWindow(AppSettings(Path(self.temp.name) / "settings.json"))
        self.window.api = self.api = ScriptedApi()
        self.api.base_url = "https://chat.office.example"
        self.window.token = "test-session"
        self.window.user = {"id": 1, "username": "alice"}
        self.window.pages.setCurrentIndex(1)
        self.window._populate_users([{"id": 1, "username": "alice"}, {"id": 2, "username": "bob"},
                                     {"id": 3, "username": "cara"}])
        self.window.show()
        self.window.activateWindow()
        self.window.composer.setFocus()
        APP.processEvents()
        # Requests use scripted responses; production foreground detection remains real.
        self.window.poll_timer.stop()
        self.window.api.cancel(self.window._message_reply)
        self.window._messages_pending = False

    def tearDown(self):
        self.window.shutdown()
        self.window.deleteLater()
        APP.processEvents()
        self.temp.cleanup()

    def marks(self):
        return [call for call in self.api.calls if call["path"] == "/api/read-receipts" and call["method"] == "POST"]

    def incoming(self, messages):
        self.window._merge_messages(messages)
        APP.processEvents()
        self.window.read_timer.stop()

    def test_active_open_team_and_dm_mark_correct_incoming_only(self):
        self.assertTrue(self.window._conversation_is_readable())
        self.incoming([message(1), message(2, sender=1)])
        self.window._maybe_mark_read()
        self.assertEqual(self.marks()[-1]["body"], {"peer_id": None, "through_id": 1})
        self.api.finish(self.marks()[-1], {"peer_id": None, "through_id": 1})
        self.window._maybe_mark_read()
        self.assertEqual(len(self.marks()), 1)
        self.window.conversations.setCurrentRow(1)
        self.window._messages_pending = False
        self.incoming([message(3, sender=2, recipient=1), message(4, sender=1, recipient=2)])
        self.window._maybe_mark_read()
        self.assertEqual(self.marks()[-1]["body"], {"peer_id": 2, "through_id": 3})

    def test_hidden_minimized_and_background_windows_never_mark(self):
        self.incoming([message(10)])
        other = QDialog()
        other.show()
        other.activateWindow()
        APP.processEvents()
        try:
            self.assertIs(APP.activeWindow(), other)
            self.window._maybe_mark_read()
            self.assertEqual(self.marks(), [])
            self.window.showMinimized()
            APP.processEvents()
            self.window._maybe_mark_read()
            self.assertEqual(self.marks(), [])
            self.window.hide()
            self.window._maybe_mark_read()
            self.assertEqual(self.marks(), [])
        finally:
            other.close()

    def test_scrolling_history_does_not_jump_or_mark_on_new_message(self):
        self.incoming([message(i, "A message\n" * 4) for i in range(1, 35)])
        scroll = self.window.transcript.verticalScrollBar()
        self.assertGreater(scroll.maximum(), 5)
        scroll.setValue(scroll.maximum() - 1)
        position = scroll.value()
        self.incoming([message(35)])
        self.assertEqual(scroll.value(), position)
        self.window._maybe_mark_read()
        self.assertEqual(self.marks(), [])
        scroll.setValue(scroll.maximum())
        self.window._maybe_mark_read()
        self.assertEqual(self.marks()[-1]["body"]["through_id"], 35)

    def test_selected_conversation_is_only_marked_after_it_is_loaded(self):
        self.incoming([message(1)])
        self.window.conversations.setCurrentRow(1)
        self.window._maybe_mark_read()
        self.assertEqual(self.marks(), [])
        pending = self.window._message_reply
        self.api.finish(pending, {"messages": [message(2, recipient=1)], "has_more": False})
        self.window._maybe_mark_read()
        self.assertEqual(self.marks()[-1]["body"], {"peer_id": 2, "through_id": 2})
        self.window.conversations.setCurrentRow(2)
        self.api.finish(self.marks()[-1], {"peer_id": 2, "through_id": 2})
        self.assertEqual(self.window._read_through, 0)

    def test_background_batch_and_open_security_dialog_do_not_mark(self):
        other = QDialog()
        other.show()
        other.activateWindow()
        APP.processEvents()
        try:
            self.window.refresh_messages()
            self.api.finish(self.window._message_reply,
                            {"messages": [message(i) for i in range(1, 51)], "has_more": True})
            APP.processEvents()
            self.window.read_timer.stop()
            self.window._maybe_mark_read()
            self.assertEqual(self.marks(), [])
            # A later batch also arriving in the background never marks delivery as read.
            self.api.finish(self.window._message_reply,
                            {"messages": [message(51)], "has_more": False})
            self.window._maybe_mark_read()
            self.assertEqual(self.marks(), [])
            other.close()
            self.window.activateWindow()
            APP.processEvents()
            security = QDialog(self.window)
            security.setModal(True)
            security.show()
            APP.processEvents()
            try:
                self.window._maybe_mark_read()
                self.assertEqual(self.marks(), [])
            finally:
                security.close()
            self.window.activateWindow()
            APP.processEvents()
            self.window.transcript.verticalScrollBar().setValue(self.window.transcript.verticalScrollBar().maximum())
            self.window._maybe_mark_read()
            self.assertEqual(self.marks()[-1]["body"]["through_id"], 51)
        finally:
            other.close()

    def test_stale_conversation_account_and_server_replies_are_ignored(self):
        self.incoming([message(1)])
        self.window._maybe_mark_read()
        old = self.marks()[-1]
        self.api.base_url = "https://new.office.example"
        self.api.finish(old, {"peer_id": None, "through_id": 500})
        self.assertEqual(self.window._read_through, 0)
        self.window._clear_session()
        self.api.finish(old, {"peer_id": None, "through_id": 500})
        self.assertEqual(self.window._read_through, 0)
        self.assertEqual(self.window._read_receipts, {})

    def test_outgoing_labels_and_scroll_position_without_mutating_messages(self):
        self.incoming([message(1, sender=1), message(2), message(3, sender=1)])
        self.window._refresh_receipts()
        get = self.api.pending("/api/read-receipts?", "GET")
        self.api.finish(get, {"peer_id": None, "receipts": [
            {"message_id": 1, "read_count": 2, "readers": [{"id": 2, "username": "bob"}, {"id": 3, "username": "cara"}]},
            {"message_id": 3, "read_count": 0, "readers": []}]})
        self.assertIn("Read by 2", self.window.transcript.toPlainText())
        self.assertEqual(self.window.transcript.toPlainText().count("Read by"), 1)
        self.assertNotIn("read_count", self.window._messages[1])
        self.window.conversations.setCurrentRow(1)
        self.window._messages_pending = False
        self.incoming([message(4, sender=1, recipient=2)])
        self.window._refresh_receipts()
        get = next(c for c in reversed(self.api.calls) if c["path"].startswith("/api/read-receipts?"))
        self.api.finish(get, {"peer_id": 2, "receipts": [{"message_id": 4, "read_count": 1,
                                                           "readers": [{"id": 2, "username": "bob"}]}]})
        self.assertIn("  ·  Read\n", self.window.transcript.toPlainText())
        self.assertNotIn("Read by", self.window.transcript.toPlainText())

    def test_invalid_receipts_never_add_reader_labels(self):
        self.incoming([message(1, sender=1), message(2)])
        for receipt in ({"message_id": 2, "read_count": 1, "readers": []},
                        {"message_id": 1, "read_count": True, "readers": []},
                        {"message_id": 1, "read_count": 1, "readers": [{"id": 1, "username": "alice"}]},
                        {"message_id": 1, "read_count": 0, "readers": [{"id": 2, "username": "bob"}]}):
            self.window._refresh_receipts()
            get = next(c for c in reversed(self.api.calls) if c["path"].startswith("/api/read-receipts?"))
            self.api.finish(get, {"peer_id": None, "receipts": [receipt]})
            self.assertEqual(self.window._read_receipts, {})

    def test_older_server_404_does_not_break_chat_or_retry_until_next_login(self):
        self.incoming([message(1)])
        self.window._maybe_mark_read()
        self.api.finish(self.marks()[-1], None, "Not Found", 404)
        self.assertEqual(self.window.token, "test-session")
        self.assertFalse(self.window._receipts_supported)
        self.window._maybe_mark_read()
        self.assertEqual(len(self.marks()), 1)
        self.assertIn("Hello", self.window.transcript.toPlainText())

    def test_revoked_receipt_session_clears_chat_and_memory(self):
        self.incoming([message(1)])
        self.window._maybe_mark_read()
        self.api.finish(self.marks()[-1], None, "Expired", 401)
        self.assertEqual(self.window.token, "")
        self.assertEqual(self.window.transcript.toPlainText(), "")
        self.assertFalse(self.window.read_timer.isActive())

    def test_pending_request_deduplicates_and_network_errors_retry(self):
        self.incoming([message(1)])
        self.window._maybe_mark_read()
        self.window._maybe_mark_read()
        self.assertEqual(len(self.marks()), 1)
        self.api.finish(self.marks()[-1], None, "Network unavailable", 0)
        self.window._maybe_mark_read()
        self.assertEqual(len(self.marks()), 2)


if __name__ == "__main__":
    unittest.main()
