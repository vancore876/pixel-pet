"""Scripted Qt Work Chat checks; no real server, account, or user settings."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QByteArray, QObject, Signal
from PySide6.QtNetwork import QNetworkRequest
from PySide6.QtWidgets import QApplication, QLineEdit

from settings import AppSettings
from work_chat import (MAX_RESPONSE_BYTES, MAX_VISIBLE_MESSAGES, WorkChatWindow,
                       _ApiClient, normalize_server_url)

APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)


def message(identifier, body="Hello", sender=2, recipient=None):
    return {"id": identifier, "sender_id": sender, "sender_username": "alice" if sender == 1 else "bob",
            "recipient_id": recipient, "body": body, "created_at": "2026-10-07T14:00:00Z"}


class ScriptedApi:
    def __init__(self):
        self.base_url = ""
        self.calls = []

    def request(self, method, path, body, callback, token=""):
        call = {"method": method, "path": path, "body": body,
                "callback": callback, "token": token, "cancelled": False, "done": False}
        self.calls.append(call)
        return call

    def cancel(self, call):
        if call:
            call["cancelled"] = True

    def cancel_all(self, except_reply=None):
        for call in self.calls:
            if not call["done"] and call is not except_reply:
                call["cancelled"] = True

    def pending(self, path, method=None):
        return next(call for call in self.calls if not call["done"]
                    and call["path"].startswith(path) and (method is None or call["method"] == method))

    def finish(self, call, data=None, error="", status=200):
        call["done"] = True
        # Deliberately deliver cancelled callbacks to exercise generation guards.
        call["callback"](data, error, status)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = AppSettings(Path(self.temp.name) / "settings.json")
        self.window = WorkChatWindow(self.settings)
        self.window.api = self.api = ScriptedApi()
        self.window.show()
        APP.processEvents()

    def tearDown(self):
        self.window.shutdown()
        self.window.deleteLater()
        APP.processEvents()
        self.temp.cleanup()

    def sign_in(self, initial=None):
        self.window.server.setText("https://chat.office.example:8443/")
        self.window.username.setText("alice")
        self.window.password.setText("an-example-password")
        self.window.authenticate()
        auth = self.api.pending("/api/auth/login")
        self.assertEqual(self.window.password.text(), "")
        self.api.finish(auth, {"token": "session-one", "user": {"id": 1, "username": "alice"}})
        self.api.finish(self.api.pending("/api/users"), {"users": [{"id": 1, "username": "alice"}, {"id": 2, "username": "bob"}]})
        self.api.finish(self.api.pending("/api/messages"), {"messages": initial or [], "has_more": False})

    def test_login_saves_only_address_and_username_and_password_is_hidden(self):
        self.sign_in()
        self.assertEqual(self.window.password.echoMode(), QLineEdit.Password)
        self.assertEqual(self.window.confirm_password.echoMode(), QLineEdit.Password)
        data = json.loads(self.settings.path.read_text())
        self.assertEqual(data["work_chat_server"], "https://chat.office.example:8443")
        self.assertEqual(data["work_chat_username"], "alice")
        self.assertNotIn("an-example-password", self.settings.path.read_text())
        self.assertNotIn("session-one", self.settings.path.read_text())
        self.assertEqual(self.window.pages.currentIndex(), 1)
        self.assertTrue(self.window.poll_timer.isActive())

    def test_office_ip_connects_and_saves_default_http_address(self):
        self.window.server.setText("192.168.50.194")
        self.window.username.setText("alice")
        self.window.password.setText("an-example-password")
        self.window.authenticate()
        self.assertEqual(self.api.base_url, "http://192.168.50.194:8765")
        self.assertEqual(self.window.server.text(), "http://192.168.50.194:8765")
        self.assertEqual(self.api.pending("/api/auth/login")["body"],
                         {"username": "alice", "password": "an-example-password"})
        saved = json.loads(self.settings.path.read_text())
        self.assertEqual(saved["work_chat_server"], "http://192.168.50.194:8765")

    def test_signup_confirmation_prevents_mistyped_password(self):
        self.window.signup.setChecked(True)
        self.assertFalse(self.window.confirm_password.isHidden())
        self.window.server.setText("http://127.0.0.1:8765")
        self.window.username.setText("alice")
        self.window.password.setText("an-example-password")
        self.window.confirm_password.setText("different-password")
        self.window.authenticate()
        self.assertFalse(self.api.calls)
        self.assertIn("do not match", self.window.status.text())
        self.window.confirm_password.setText("an-example-password")
        self.window.authenticate()
        self.assertEqual(self.api.calls[-1]["path"], "/api/auth/register")
        self.assertEqual(self.window.confirm_password.text(), "")

    def test_stale_conversation_response_never_enters_new_transcript(self):
        self.sign_in([message(1, "Team message")])
        self.window.refresh_messages()
        team_call = self.api.pending("/api/messages")
        self.window.conversations.setCurrentRow(1)
        self.assertEqual(self.window._peer_id, 2)
        self.assertTrue(team_call["cancelled"])
        self.api.finish(team_call, {"messages": [message(2, "Must stay in Team Room")], "has_more": False})
        self.assertNotIn("Must stay", self.window.transcript.toPlainText())
        direct = self.api.pending("/api/messages")
        self.assertIn("peer_id=2", direct["path"])
        self.api.finish(direct, {"messages": [message(3, "Private coworker message", recipient=1)], "has_more": False})
        self.assertIn("Private coworker message", self.window.transcript.toPlainText())
        self.assertNotIn("Team message", self.window.transcript.toPlainText())

    def test_signout_clears_token_and_late_reply_is_ignored(self):
        self.sign_in()
        self.window.refresh_messages()
        old = self.api.pending("/api/messages")
        self.window.composer.setPlainText("Private draft")
        self.window.sign_out()
        self.assertEqual(self.window.token, "")
        self.assertIsNone(self.window.user)
        self.assertEqual(self.window.composer.toPlainText(), "")
        self.assertFalse(self.window._drafts)
        self.assertFalse(self.window.poll_timer.isActive())
        self.assertEqual(self.window.pages.currentIndex(), 0)
        logout = self.api.pending("/api/auth/logout")
        self.assertEqual(logout["token"], "session-one")
        self.api.finish(old, {"messages": [message(9, "Late private reply")], "has_more": False})
        self.assertEqual(self.window.transcript.toPlainText(), "")

    def test_message_text_is_literal_and_failed_send_preserves_composer(self):
        literal = '<b>Hi</b> <img src="file:///private"> & <script>example()</script>'
        self.sign_in([message(1, literal)])
        self.assertIn(literal, self.window.transcript.toPlainText())
        self.window.composer.setPlainText("Keep this draft")
        self.window.send_message()
        self.assertFalse(self.window.send_button.isEnabled())
        self.api.finish(self.api.pending("/api/messages", "POST"), None, "Office server is unavailable", 503)
        self.assertEqual(self.window.composer.toPlainText(), "Keep this draft")
        self.assertTrue(self.window.send_button.isEnabled())
        self.assertIn("unavailable", self.window.status.text())

    def test_send_response_does_not_skip_older_posts_or_duplicate_echoes(self):
        self.sign_in([message(1, "First")])
        self.window.refresh_messages()
        polling = self.api.pending("/api/messages", "GET")
        self.window.composer.setPlainText("My sent text")
        self.window.send_message()
        sent = message(4, "My sent text", sender=1)
        self.api.finish(self.api.pending("/api/messages", "POST"), {"message": sent}, status=201)
        self.assertEqual(self.window._cursor, 1)
        self.assertEqual(self.window.composer.toPlainText(), "")
        self.api.finish(polling, {"messages": [message(2, "Coworker two"), message(3, "Coworker three")], "has_more": False})
        self.assertEqual(self.window._cursor, 3)
        self.window.refresh_messages()
        next_poll = self.api.pending("/api/messages", "GET")
        self.assertIn("after_id=3", next_poll["path"])
        self.api.finish(next_poll, {"messages": [sent], "has_more": False})
        text = self.window.transcript.toPlainText()
        self.assertEqual(text.count("My sent text"), 1)
        self.assertIn("Coworker two", text)
        self.assertIn("Coworker three", text)

    def test_drafts_follow_conversations_and_pending_send_cannot_clear_new_draft(self):
        self.sign_in()
        self.window.composer.setPlainText("Team draft")
        self.window.send_message()
        sending = self.api.pending("/api/messages", "POST")
        self.window.conversations.setCurrentRow(1)
        self.window.composer.setPlainText("Bob draft")
        self.assertFalse(sending["cancelled"])
        self.assertFalse(self.window.send_button.isEnabled())
        self.api.finish(sending, {"message": message(1, "Team draft", sender=1)}, status=201)
        self.assertEqual(self.window.composer.toPlainText(), "Bob draft")
        self.assertEqual(self.window.transcript.toPlainText(), "")
        self.window.conversations.setCurrentRow(0)
        self.assertEqual(self.window.composer.toPlainText(), "")

    def test_hidden_window_stops_requests_and_resumes_memory_session(self):
        self.sign_in()
        self.window.refresh_messages()
        pending = self.api.pending("/api/messages")
        self.window.hide()
        self.assertFalse(self.window.poll_timer.isActive())
        self.assertTrue(pending["cancelled"])
        before = len(self.api.calls)
        self.window._poll()
        self.assertEqual(len(self.api.calls), before)
        self.assertEqual(self.window.token, "session-one")
        self.api.finish(pending, {"messages": [message(12, "Hidden late reply")], "has_more": False})
        self.assertEqual(self.window.transcript.toPlainText(), "")
        self.window.show()
        APP.processEvents()
        self.assertTrue(self.window.poll_timer.isActive())
        self.assertGreater(len(self.api.calls), before)

    def test_pending_send_finishes_while_hidden_and_clears_its_matching_draft(self):
        self.sign_in()
        self.window.composer.setPlainText("Deliver only once")
        self.window.send_message()
        sending = self.api.pending("/api/messages", "POST")
        self.window.hide()
        self.assertFalse(sending["cancelled"])
        self.api.finish(sending, {"message": message(8, "Deliver only once", sender=1)}, status=201)
        self.assertFalse(self.window._send_pending)
        self.assertEqual(self.window.composer.toPlainText(), "")
        self.assertEqual(self.window.transcript.toPlainText(), "")

    def test_401_ends_session_and_invalid_recipient_messages_are_rejected(self):
        self.sign_in()
        self.window.refresh_messages()
        self.api.finish(self.api.pending("/api/messages"), {"messages": [message(1, "Wrong private room", recipient=1)], "has_more": False})
        self.assertEqual(self.window.transcript.toPlainText(), "")
        self.assertIn("invalid message", self.window.status.text())
        self.window.refresh_users()
        self.api.finish(self.api.pending("/api/users"), None, "Expired", 401)
        self.assertEqual(self.window.token, "")
        self.assertEqual(self.window.pages.currentIndex(), 0)

    def test_visible_history_is_bounded_and_refresh_uses_small_batches(self):
        self.sign_in()
        self.window.refresh_messages()
        call = self.api.pending("/api/messages")
        self.assertIn("limit=50", call["path"])
        self.api.finish(call, {"messages": [message(i, f"Message {i}") for i in range(1, 51)], "has_more": True})
        APP.processEvents()
        followup = self.api.pending("/api/messages")
        self.assertIn("after_id=50", followup["path"])
        self.api.finish(followup, {"messages": [], "has_more": False})
        self.window._merge_messages([message(i, f"Message {i}") for i in range(51, 601)])
        self.assertEqual(len(self.window._messages), MAX_VISIBLE_MESSAGES)
        self.assertEqual(min(self.window._messages), 101)

    def test_logout_and_new_server_ignore_old_auth_response(self):
        self.window.server.setText("https://first.office.example")
        self.window.username.setText("alice")
        self.window.password.setText("an-example-password")
        self.window.authenticate()
        old_auth = self.api.pending("/api/auth/login")
        self.window.sign_out()
        self.window.server.setText("https://second.office.example")
        self.window.password.setText("another-example-password")
        self.window.authenticate()
        self.api.finish(old_auth, {"token": "stale-token", "user": {"id": 1, "username": "alice"}})
        self.assertEqual(self.window.token, "")
        self.assertEqual(self.api.base_url, "https://second.office.example")


class UrlTests(unittest.TestCase):
    def test_bare_office_ip_gets_default_http_port(self):
        for value, expected in ((" 192.168.50.194 ", "http://192.168.50.194:8765"),
                                ("10.20.30.40", "http://10.20.30.40:8765"),
                                ("172.31.255.254:9000", "http://172.31.255.254:9000"),
                                ("fd00::194", "http://[fd00::194]:8765"),
                                ("[FD00:0:0:0:0:0:0:194]", "http://[fd00::194]:8765"),
                                ("[fd00::194]:9000", "http://[fd00::194]:9000"),
                                ("localhost", "http://localhost:8765")):
            with self.subTest(value=value):
                self.assertEqual(normalize_server_url(value), expected)

    def test_explicit_http_accepts_only_office_and_loopback_addresses(self):
        for value, expected in (("http://192.168.50.194:8765/", "http://192.168.50.194:8765"),
                                ("http://10.0.0.1", "http://10.0.0.1"),
                                ("http://172.16.0.1:9000", "http://172.16.0.1:9000"),
                                ("http://[fc00::1]:8765", "http://[fc00::1]:8765"),
                                ("http://[::ffff:192.168.50.194]:8765", "http://[::ffff:192.168.50.194]:8765"),
                                ("http://127.0.0.1:8765", "http://127.0.0.1:8765"),
                                ("http://127.0.0.2", "http://127.0.0.2"),
                                ("http://localhost/", "http://localhost"),
                                ("http://[::1]:8765", "http://[::1]:8765")):
            with self.subTest(value=value):
                self.assertEqual(normalize_server_url(value), expected)

    def test_https_remains_available_for_verified_server_names_and_public_ips(self):
        for value, expected in ((" https://CHAT.office.example:8443/ ", "https://chat.office.example:8443"),
                                ("https://8.8.8.8", "https://8.8.8.8"),
                                ("https://[2001:4860:4860::8888]", "https://[2001:4860:4860::8888]")):
            with self.subTest(value=value):
                self.assertEqual(normalize_server_url(value), expected)

    def test_plaintext_outside_office_and_ambiguous_numeric_addresses_are_rejected(self):
        for value in ("8.8.8.8", "http://8.8.8.8:8765", "http://172.15.255.255", "http://172.32.0.1",
                      "http://192.169.0.1", "http://169.254.10.20", "http://100.64.0.1",
                      "http://0.0.0.0", "http://255.255.255.255", "http://[::]", "http://[fe80::1]",
                      "http://[2001:db8::1]", "http://[2001:4860:4860::8888]", "http://[::ffff:8.8.8.8]",
                      "office-server", "http://office-server", "http://localhost.evil.example",
                      "http://2130706433", "http://127.1", "http://0177.0.0.1", "http://0x7f000001",
                      "192.168.050.194", "https://2130706433", "https://127.1", "https://0x7f.0.0.1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_server_url(value)

    def test_invalid_origins_are_rejected(self):
        for value in ("", "192.168.50.194:0", "192.168.50.194:65536", "192.168.50.194:",
                      "192.168.50.194/api", "user:pass@192.168.50.194", "http://[fd00::1%eth0]",
                      "https://user:pass@office-server", "https://office-server/api", "https://office-server?",
                      "https://office-server#", "https://office-server:0", "https://office-server:65536",
                      "https://office-server:", "https://office-server\\@evil", "https://office\nserver", "file:///server"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_server_url(value)


class FakeReply(QObject):
    readyRead = Signal()
    finished = Signal()
    sslErrors = Signal(object)

    def __init__(self):
        super().__init__()
        self.data = bytearray()
        self.status = 200
        self.aborted = False
        self.complete = False

    def setReadBufferSize(self, size):
        self.buffer_limit = size

    def read(self, amount):
        chunk = self.data[:amount]
        del self.data[:amount]
        return QByteArray(bytes(chunk))

    def attribute(self, name):
        return self.status

    def abort(self):
        self.aborted = True
        self.finish()

    def finish(self):
        if not self.complete:
            self.complete = True
            self.finished.emit()


class FakeManager:
    def __init__(self):
        self.requests = []

    def get(self, request):
        reply = FakeReply()
        self.requests.append((request, None, reply))
        return reply

    def post(self, request, payload):
        reply = FakeReply()
        self.requests.append((request, payload, reply))
        return reply


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.client = _ApiClient()
        self.client.manager = self.manager = FakeManager()
        self.client.base_url = "https://chat.office.example"
        self.results = []

    def tearDown(self):
        self.client.cancel_all()
        self.client.deleteLater()
        APP.processEvents()

    def request(self):
        return self.client.request("GET", "/api/users", None,
            lambda data, error, status: self.results.append((data, error, status)), "test-token")

    def test_manual_redirect_policy_rejects_redirect_response(self):
        reply = self.request()
        request = self.manager.requests[-1][0]
        self.assertEqual(request.attribute(QNetworkRequest.RedirectPolicyAttribute), QNetworkRequest.ManualRedirectPolicy)
        self.assertEqual(bytes(request.rawHeader("Authorization")), b"Bearer test-token")
        reply.status = 302
        reply.finish()
        self.assertIn("redirected", self.results[0][1])
        self.assertEqual(len(self.manager.requests), 1)

    def test_error_detail_from_server_is_readable(self):
        reply = self.request()
        reply.status = 409
        reply.data.extend(json.dumps({"detail": "That username is already registered."}).encode())
        reply.finish()
        self.assertEqual(self.results[0][1], "That username is already registered.")

    def test_oversize_response_aborts_without_decoding(self):
        reply = self.request()
        reply.data.extend(b"x" * (MAX_RESPONSE_BYTES + 1))
        reply.readyRead.emit()
        self.assertTrue(reply.aborted)
        self.assertEqual(len(self.results), 1)
        self.assertIn("too large", self.results[0][1])
        self.assertFalse(self.client._pending)

    def test_timeout_and_certificate_errors_abort(self):
        reply = self.request()
        self.client._timeout(reply)
        self.assertTrue(reply.aborted)
        self.assertIn("10 seconds", self.results[-1][1])
        reply = self.request()
        reply.sslErrors.emit([])
        self.assertTrue(reply.aborted)
        self.assertIn("certificate", self.results[-1][1])

    def test_cancel_discards_callback_and_invalid_json_reports_error(self):
        reply = self.request()
        self.client.cancel_all()
        self.assertTrue(reply.aborted)
        self.assertFalse(self.results)
        reply = self.request()
        reply.data.extend(b"not JSON")
        reply.finish()
        self.assertIn("invalid response", self.results[0][1])


if __name__ == "__main__":
    unittest.main()
