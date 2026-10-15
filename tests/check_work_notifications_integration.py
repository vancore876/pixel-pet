"""Exercise background popups against a disposable real Work Chat server.

Run with the desktop interpreter, as with check_work_chat_integration.py.
Only test accounts, settings and delivery watermarks in a temporary directory
are used. No existing server or office data is contacted.
"""
import json
import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests import check_work_chat_integration as chat_check
from work_notification_popup import WorkNotificationPopup
from work_notifications import WorkNotificationService


@unittest.skipUnless(chat_check.SERVER_PYTHON.is_file(), "Optional server interpreter is not installed")
class RealNotificationTests(chat_check.RealServerTests):
    def test_background_all_conversations_quiet_reconnect_and_private_scope(self):
        alice = self.account("notify_alice")
        bob = self.account("notify_bob")
        carol = self.account("notify_carol")
        self.choose(bob, carol.user["id"])
        state = Path(self.temp.name) / "notification-cursor.json"
        service = WorkNotificationService(bob.settings, state)
        popup = WorkNotificationPopup(bob.settings)
        offers = []
        self.addCleanup(service.stop)
        self.addCleanup(popup.shutdown)

        def show(row):
            self.assertTrue(popup.present(row))
            offers.append(dict(row))
            self.assertTrue(service.mark_notification_presented(row))
            popup.dismiss()

        service.notification.connect(show)
        service.session_ended.connect(popup.clear_session)
        service.session_changed(bob.api.base_url, bob.token, bob.user)
        chat_check.wait_for(lambda: state.is_file() and not service._request_pending)
        self.assertEqual(offers, [])
        bob.hide()

        def send(window, body, peer=None):
            self.choose(window, peer)
            window.composer.setPlainText(body)
            window.send_message()
            chat_check.wait_for(lambda: not window._send_pending)
            self.assertEqual(window.composer.toPlainText(), "", window.status.text())

        send(alice, "Team popup while Bob is hidden")
        service.pump()
        chat_check.wait_for(lambda: len(offers) == 1)
        self.assertIsNone(offers[-1]["peer_id"])
        send(alice, "Personal popup while Bob views Carol", bob.user["id"])
        service.pump()
        chat_check.wait_for(lambda: len(offers) == 2)
        self.assertEqual(offers[-1]["peer_id"], alice.user["id"])
        self.assertFalse(bob.isVisible())
        self.assertEqual(bob._peer_id, carol.user["id"])

        send(alice, "Only Carol may receive this", carol.user["id"])
        bob.show()
        send(bob, "Bob's own team message")
        service.pump()
        chat_check.wait_for(lambda: not service._request_pending)
        self.assertEqual(len(offers), 2)

        # A queued, unshown message must survive sign-out/reconnect by its
        # earlier durable cursor, even though no message body is stored locally.
        bob.settings.save({"quiet_mode": True})
        send(alice, "Quiet mode message survives reconnect", bob.user["id"])
        service.pump()
        chat_check.wait_for(lambda: not service._request_pending)
        self.assertEqual(len(offers), 2)
        self.assertEqual(len(service._queue), 1)
        service.session_changed("", "", None)
        bob.settings.save({"quiet_mode": False})
        service.session_changed(bob.api.base_url, bob.token, bob.user)
        chat_check.wait_for(lambda: len(offers) == 3)
        self.assertEqual(offers[-1]["body"], "Quiet mode message survives reconnect")
        self.assertTrue(bob.open_notification(offers[-1]))
        chat_check.wait_for(lambda: not bob._messages_pending)
        self.assertEqual(bob._peer_id, alice.user["id"])
        self.assertIn(offers[-1]["body"], bob.transcript.toPlainText())
        self.assertNotIn("Only Carol", bob.transcript.toPlainText())
        saved = state.read_text()
        self.assertEqual(len(json.loads(saved)), 1)
        for value in ("notify_bob", "Quiet mode", bob.token, self.origin):
            self.assertNotIn(value, saved)

        # First use on a fresh PC starts at the login transaction's watermark,
        # so messages arriving before its first feed response still pop up.
        service.stop()
        login = []
        api = chat_check._ApiClient()
        api.base_url = self.origin
        api.request("POST", "/api/auth/login", {"username": "notify_bob", "password": "disposable-password-123"},
                    lambda data, error, status: login.append((data, error, status)))
        chat_check.wait_for(lambda: bool(login))
        data, error, status = login[0]
        self.assertEqual(error, "")
        self.assertEqual(status, 200)
        self.assertIsInstance(data.get("notification_cursor"), int)
        send(alice, "Arrived between login and first feed", bob.user["id"])
        race = WorkNotificationService(bob.settings, Path(self.temp.name) / "new-pc-cursor.json")
        self.addCleanup(race.stop)
        race_offers = []

        def show_race(row):
            self.assertTrue(popup.present(row))
            race_offers.append(row)
            self.assertTrue(race.mark_notification_presented(row))
            popup.dismiss()

        race.notification.connect(show_race)
        race.session_changed(self.origin, data["token"],
                             {**data["user"], "notification_cursor": data["notification_cursor"]})
        chat_check.wait_for(lambda: bool(race_offers))
        self.assertEqual([row["body"] for row in race_offers], ["Arrived between login and first feed"])


if __name__ == "__main__":
    suite = unittest.TestSuite([RealNotificationTests(
        "test_background_all_conversations_quiet_reconnect_and_private_scope")])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
