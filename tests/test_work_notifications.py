"""Background Work Chat notifications use a separate durable delivery cursor."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication

from config import DEFAULTS
from settings import AppSettings
from work_chat import WorkChatWindow
from work_notification_popup import WorkNotificationPopup
from work_notifications import WorkNotificationService, MAX_QUEUED_NOTIFICATIONS

APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)


def message(identifier, recipient=None, sender=2, body="Pick up the order"):
    return {"id": identifier, "sender_id": sender, "sender_username": "bob",
            "recipient_id": recipient, "body": body, "created_at": "2026-10-14T14:00:00Z"}


class ScriptedApi:
    def __init__(self):
        self.base_url = ""
        self.calls = []

    def request(self, method, path, body, callback, token=""):
        call = {"method": method, "path": path, "body": body, "callback": callback,
                "token": token, "cancelled": False, "done": False}
        self.calls.append(call)
        return call

    def cancel_all(self):
        for call in self.calls:
            if not call["done"]:
                call["cancelled"] = True

    def cancel(self, call):
        if call:
            call["cancelled"] = True

    def pending(self):
        return next(call for call in self.calls if not call["done"] and not call["cancelled"])

    def finish(self, data=None, error="", status=200, call=None):
        call = call or self.pending()
        call["done"] = True
        call["callback"](data, error, status)


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "notifications.json"
        self.settings = dict(DEFAULTS, quiet_mode=False, work_chat_notifications=True,
                             work_chat_reminder_minutes=30)
        self.ready = True
        self.service = WorkNotificationService(self.settings, self.path, can_notify=lambda: self.ready)
        self.service.api = self.api = ScriptedApi()
        self.offers = []
        self.errors = []
        self.service.notification.connect(self.offers.append)
        self.service.error.connect(self.errors.append)

    def tearDown(self):
        self.service.stop()
        self.service.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        self.temp.cleanup()

    def attach(self, user_id=1, server="http://127.0.0.1:8765"):
        self.service.session_changed(server, "private-bearer-token", {"id": user_id, "username": "alice"})

    def baseline(self, cursor=20):
        self.attach()
        self.assertNotIn("after_id", self.api.pending()["path"])
        self.api.finish({"messages": [], "cursor": cursor, "latest_id": cursor, "has_more": False})

    def receive(self, rows, cursor=None, more=False):
        self.service.pump()
        self.api.finish({"messages": rows, "cursor": cursor if cursor is not None else rows[-1]["id"],
                         "latest_id": cursor or (rows[-1]["id"] if rows else 0), "has_more": more})

    def saved_cursor(self):
        return next(iter(json.loads(self.path.read_text()).values()))

    def test_first_connection_baselines_without_historical_notifications(self):
        self.baseline(200)
        self.assertEqual(self.offers, [])
        self.assertEqual(self.saved_cursor(), 200)
        self.service.pump()
        self.assertEqual(parse_qs(urlsplit(self.api.pending()["path"]).query)["after_id"], ["200"])

    def test_authentication_snapshot_preserves_messages_before_first_feed(self):
        self.service.session_changed("http://127.0.0.1:8765", "token",
                                     {"id": 1, "username": "alice", "notification_cursor": 20})
        self.assertIn("after_id=20", self.api.pending()["path"])
        self.assertEqual(self.saved_cursor(), 20)
        self.api.finish({"messages": [message(21)], "cursor": 21, "has_more": False})
        self.assertEqual(self.offers[-1]["id"], 21)

    def test_work_chat_emits_login_watermark_without_changing_account_identity(self):
        window = WorkChatWindow(AppSettings(Path(self.temp.name) / "settings.json"))
        api = ScriptedApi()
        window.api = api
        sessions = []
        window.session_changed.connect(lambda server, token, user: sessions.append((server, token, user)))
        try:
            window.server.setText("http://127.0.0.1:8765")
            window.username.setText("alice")
            window.password.setText("example-password")
            window.authenticate()
            api.finish({"token": "session-token", "user": {"id": 1, "username": "alice"},
                        "notification_cursor": 20})
            self.assertEqual(window.user, {"id": 1, "username": "alice"})
            self.assertEqual(sessions[-1][2], {"id": 1, "username": "alice", "notification_cursor": 20})
        finally:
            window.shutdown()
            window.deleteLater()

    def test_saved_delivery_position_takes_precedence_over_new_auth_snapshot(self):
        self.baseline(20)
        self.service.session_changed("http://127.0.0.1:8765", "token",
                                     {"id": 1, "username": "alice", "notification_cursor": 45})
        self.assertIn("after_id=20", self.api.pending()["path"])

    def test_hidden_chat_and_all_conversations_feed_one_popup_per_message(self):
        self.baseline()
        self.receive([message(21), message(23, recipient=1, sender=3), message(26, recipient=1)])
        self.assertEqual([row["id"] for row in self.offers], [21])
        self.assertIsNone(self.offers[-1]["peer_id"])
        self.assertEqual(self.saved_cursor(), 20)
        self.service.mark_notification_presented(self.offers[-1])
        self.ready = False
        self.service.pump()
        self.assertEqual(len(self.offers), 1)
        self.ready = True
        self.service.pump()
        self.assertEqual(self.offers[-1]["peer_id"], 3)
        self.service.mark_notification_presented(self.offers[-1])
        self.service.pump()
        self.assertEqual(self.offers[-1]["peer_id"], 2)
        self.service.mark_notification_presented(self.offers[-1])
        self.assertEqual(self.saved_cursor(), 26)

    def test_quiet_mode_defers_and_restart_recovers_unshown_cards(self):
        self.baseline()
        self.settings["quiet_mode"] = True
        self.receive([message(21), message(22)])
        self.assertEqual(self.offers, [])
        self.assertEqual(self.saved_cursor(), 20)
        self.service.session_changed("", "", None)
        self.settings["quiet_mode"] = False
        self.attach()
        self.assertIn("after_id=20", self.api.pending()["path"])
        self.api.finish({"messages": [message(21), message(22)], "cursor": 22, "has_more": False})
        self.assertEqual(self.offers[-1]["id"], 21)

    def test_disabled_notifications_keep_cursor_until_reenabled(self):
        self.baseline()
        self.settings["work_chat_notifications"] = False
        self.receive([message(21)])
        self.assertEqual(self.offers, [])
        self.assertEqual(self.saved_cursor(), 20)
        self.settings["work_chat_notifications"] = True
        self.service.pump()
        self.assertEqual(self.offers[-1]["id"], 21)

    def test_popup_refusal_releases_offer_without_losing_delivery(self):
        self.baseline()
        self.receive([message(21)])
        refused = self.offers[-1]
        self.service.release_notification(refused)
        self.assertEqual(self.saved_cursor(), 20)
        self.service.pump()
        self.assertEqual([row["id"] for row in self.offers], [21, 21])
        self.assertTrue(self.service.mark_notification_presented(self.offers[-1]))
        self.assertEqual(self.saved_cursor(), 21)

    def test_no_message_body_username_server_or_token_is_persisted(self):
        self.baseline()
        self.receive([message(21, recipient=1, body="Private customer information")])
        self.service.mark_notification_presented(self.offers[-1])
        text = self.path.read_text()
        for secret in ("Private", "alice", "bob", "127.0.0.1", "private-bearer-token"):
            self.assertNotIn(secret, text)
        saved = json.loads(text)
        self.assertEqual(list(saved.values()), [21])
        self.assertEqual(len(next(iter(saved))), 64)

    def test_saved_accounts_and_servers_have_independent_positions(self):
        self.baseline(20)
        self.attach(user_id=3)
        self.assertNotIn("after_id", self.api.pending()["path"])
        self.api.finish({"messages": [], "cursor": 90, "has_more": False})
        self.attach(server="http://127.0.0.1:8766")
        self.assertNotIn("after_id", self.api.pending()["path"])
        self.api.finish({"messages": [], "cursor": 5, "has_more": False})
        self.attach()
        self.assertIn("after_id=20", self.api.pending()["path"])

    def test_logout_cancels_late_callbacks_and_clears_all_message_text(self):
        self.baseline()
        self.service.pump()
        old = self.api.pending()
        self.service.session_changed("", "", None)
        self.api.finish({"messages": [message(21)], "cursor": 21}, call=old)
        self.assertEqual(self.offers, [])
        self.assertEqual(list(self.service._queue), [])
        self.assertEqual(self.service._reminders, {})
        self.assertFalse(self.service.timer.isActive())
        self.assertEqual(self.service._token, "")

    def test_self_and_other_employees_personal_messages_are_rejected(self):
        self.baseline()
        for row in (message(21, sender=1), message(21, recipient=4)):
            self.receive([row])
            self.assertEqual(self.offers, [])
            self.assertEqual(self.saved_cursor(), 20)
        self.assertTrue(self.errors)

    def test_invalid_batch_does_not_advance_or_partially_deliver(self):
        self.baseline()
        for rows, cursor in (([message(22), message(21)], 21), ([message(21), message(21)], 21),
                             ([message(21)], 25), ([message(19)], 19)):
            self.receive(rows, cursor)
            self.assertEqual(self.offers, [])
            self.assertEqual(self.saved_cursor(), 20)

    def test_full_queue_stops_fetching_and_leaves_cursor_on_server(self):
        self.baseline(0)
        self.settings["quiet_mode"] = True
        self.receive([message(index) for index in range(1, 51)], more=True)
        self.receive([message(index) for index in range(51, 101)], more=True)
        calls = len(self.api.calls)
        self.service.pump()
        self.assertEqual(len(self.api.calls), calls)
        self.assertEqual(len(self.service._queue), MAX_QUEUED_NOTIFICATIONS)
        self.assertEqual(self.saved_cursor(), 0)
        self.settings["quiet_mode"] = False
        self.service.pump()
        self.service.mark_notification_presented(self.offers[-1])
        self.service.pump()
        query = parse_qs(urlsplit(self.api.pending()["path"]).query)
        self.assertEqual(query["limit"], ["1"])
        self.assertEqual(query["after_id"], ["100"])

    def test_empty_page_does_not_skip_older_queued_message(self):
        self.baseline()
        self.settings["quiet_mode"] = True
        self.receive([message(21)])
        self.receive([], cursor=25)
        self.assertEqual(self.saved_cursor(), 20)
        self.settings["quiet_mode"] = False
        self.service.pump()
        self.service.mark_notification_presented(self.offers[-1])
        self.assertEqual(self.saved_cursor(), 21)

    def test_current_session_reminder_snooze_and_ack_are_personal(self):
        self.baseline()
        with patch("work_notifications.time.time", return_value=100):
            self.receive([message(21)])
            self.service.mark_notification_presented(self.offers[-1])
        with patch("work_notifications.time.time", return_value=1899):
            self.service.pump()
            self.assertEqual(len(self.offers), 1)
        with patch("work_notifications.time.time", return_value=1901):
            self.service.pump()
            self.assertEqual(self.offers[-1]["notification_kind"], "reminder")
            self.service.mark_notification_presented(self.offers[-1])
            self.service.snooze(21, 5)
        with patch("work_notifications.time.time", return_value=2200):
            self.service.pump()
            self.assertEqual(len(self.offers), 2)
        with patch("work_notifications.time.time", return_value=2202):
            self.service.pump()
            self.assertEqual(len(self.offers), 3)
            self.service.acknowledge(21)
        self.assertEqual(self.service._reminders, {})
        self.assertEqual(self.saved_cursor(), 21)

    def test_zero_minutes_disables_followups_without_disabling_initial_popup(self):
        self.settings["work_chat_reminder_minutes"] = 0
        self.baseline()
        self.receive([message(21)])
        self.assertEqual(len(self.offers), 1)
        self.service.mark_notification_presented(self.offers[-1])
        self.assertEqual(self.service._reminders, {})

    def test_unauthorized_clears_session_and_network_failure_can_retry(self):
        expired = []
        self.service.auth_expired.connect(lambda: expired.append(True))
        self.baseline()
        self.assertEqual(expired, [])
        self.service.pump()
        self.api.finish(error="Network unavailable", status=0)
        self.assertTrue(self.service.timer.isActive())
        self.service.pump()
        self.api.finish(error="Session ended", status=401)
        self.assertFalse(self.service.timer.isActive())
        self.assertEqual(self.service._token, "")
        self.assertEqual(expired, [True])


class PopupTests(unittest.TestCase):
    def setUp(self):
        self.settings = dict(DEFAULTS, quiet_mode=False, work_chat_notifications=True)
        self.popup = WorkNotificationPopup(self.settings)

    def tearDown(self):
        self.popup.shutdown()
        self.popup.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_plain_text_team_personal_routing_and_no_focus_steal(self):
        opened = []
        self.popup.conversation_requested.connect(opened.append)
        row = dict(message(21, recipient=1, body="<b>Call customer</b>"), peer_id=2)
        self.assertTrue(self.popup.present(row))
        self.assertEqual(self.popup.body.toPlainText(), "<b>Call customer</b>")
        self.assertEqual(self.popup.heading.text(), "New personal message")
        self.assertEqual(self.popup.details.text(), "From: bob")
        self.assertTrue(self.popup.testAttribute(Qt.WA_ShowWithoutActivating))
        self.popup.open_button.click()
        self.assertEqual(opened[-1]["peer_id"], 2)
        self.popup.present(dict(message(22), peer_id=None))
        self.popup.open_button.click()
        self.assertIsNone(opened[-1]["peer_id"])
        self.assertEqual(self.popup.heading.text(), "New team room message")

    def test_dismiss_is_not_acknowledge_and_logout_wipes_content(self):
        acknowledged = []
        self.popup.acknowledge_requested.connect(acknowledged.append)
        self.popup.present(message(21, body="Private customer information"))
        self.popup.dismiss_button.click()
        self.assertEqual(acknowledged, [])
        self.popup.clear_session()
        self.assertEqual(self.popup.notification, {})
        self.assertEqual(self.popup.body.toPlainText(), "")
        self.assertEqual(self.popup.identifier, "")
        self.assertFalse(self.popup.isVisible())

    def test_quiet_mode_refuses_popup_and_snooze_keeps_expected_id(self):
        self.settings["quiet_mode"] = True
        self.assertFalse(self.popup.present(message(21)))
        self.settings["quiet_mode"] = False
        snoozed = []
        self.popup.snooze_requested.connect(lambda identifier, minutes: snoozed.append((identifier, minutes)))
        self.popup.present(message(21))
        self.popup.snooze_minutes.setCurrentIndex(1)
        self.popup.snooze_button.click()
        self.assertEqual(snoozed, [("21", 15)])


if __name__ == "__main__":
    unittest.main()
