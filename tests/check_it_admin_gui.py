"""Real desktop IT setup and employee management against a disposable HTTP server."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["GROQ_API_KEY"] = ""
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from main import BuddyApp
from settings import AppSettings
import test_work_server as server_tests

APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)
IT_PASSWORD = "disposable private IT passphrase"
NEW_EMPLOYEE_PASSWORD = "disposable replacement employee passphrase"


def wait_for(predicate, description, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        APP.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for " + description)


class NoCredentials:
    def get(self):
        return ""


@unittest.skipUnless(server_tests.SERVER_AVAILABLE, "Install requirements-server.txt")
class ITDesktopIntegrationTests(unittest.TestCase):
    setUpClass = classmethod(server_tests.WorkServerHTTPTests.setUpClass.__func__)
    tearDownClass = classmethod(server_tests.WorkServerHTTPTests.tearDownClass.__func__)
    request = server_tests.WorkServerHTTPTests.request

    def test_setup_reset_signout_and_separate_employee_identity(self):
        root = Path(self.directory.name)
        contexts = [
            patch("main.CredentialStore", return_value=NoCredentials()),
            patch("config.team_data_directory", return_value=root / "team-profile"),
            patch("work_notifications.team_data_directory", return_value=root / "team-profile"),
            patch.dict(os.environ, {"LOCALAPPDATA": str(root / "local-appdata")}),
        ]
        for context in contexts:
            context.start()
        buddy = None
        try:
            settings = AppSettings(root / "desktop" / "settings.json")
            settings.save({"desktop_enabled": False, "mouse_mode": "off", "ai_autonomy": False,
                "memory_ai": False, "business_greetings": False, "home_on_start": False,
                "semantic_memory_enabled": False, "pet_enabled": False, "team_sync_enabled": False,
                "work_chat_server": self.address})
            buddy = BuddyApp(APP, settings, show_tray=False)
            buddy.note_service.stop()
            buddy.pet.hide()
            buddy.overlay.hide()

            def employee(username, *, register=True, password=server_tests.PASSWORD):
                status, data, _ = self.request("/api/auth/" + ("register" if register else "login"),
                    method="POST", data={"username": username, "password": password})
                self.assertEqual(status, 201 if register else 200, data)
                return data

            alice = employee("counter_alice")
            alice_second = employee("counter_alice", register=False)
            ordinary_admin = employee("admin")
            self.assertEqual(self.request("/api/it/users", token=ordinary_admin["token"])[0], 401)
            self.request("/api/messages", method="POST", token=alice["token"],
                data={"body": "Preserve this pickup instruction", "recipient_id": ordinary_admin["user"]["id"]})

            buddy.show_home()
            buddy.home.it_button.click()
            window = buddy.it_window
            self.assertTrue(window.isVisible())
            self.assertEqual(window.server_input.text(), self.address)
            window.password_input.setText("admin123")
            QTest.keyClick(window.password_input, Qt.Key_Return)
            wait_for(lambda: bool(window.token), "initial IT login")
            self.assertIs(window.pages.currentWidget(), window.password_page)
            bootstrap = window.token
            self.assertEqual(self.request("/api/it/users", token=bootstrap)[0], 403)
            window.current_password.setText("admin123")
            window.new_password.setText(IT_PASSWORD)
            window.confirm_password.setText(IT_PASSWORD)
            QTest.keyClick(window.confirm_password, Qt.Key_Return)
            wait_for(lambda: window.pages.currentWidget() is window.dashboard_page and not window._refreshing,
                     "IT password change and dashboard")
            self.assertNotEqual(window.token, bootstrap)
            self.assertEqual(self.request("/api/it/users", token=bootstrap)[0], 401)
            self.assertEqual(self.request("/api/messages", token=window.token)[0], 401)
            self.assertEqual(window.metrics["users"].text(), "2")
            self.assertEqual(window.metrics["sessions"].text(), "3")
            self.assertEqual(window.metrics["messages"].text(), "1")
            self.assertFalse(buddy.work_chat.token)

            window.search.setText("counter_alice")
            self.assertEqual(window.users_table.rowCount(), 1)
            window.users_table.selectRow(0)
            window.reset_button.click()
            dialog = window._action_dialog
            self.assertTrue(dialog.isVisible())
            dialog.admin_password.setText(IT_PASSWORD)
            dialog.new_password.setText(NEW_EMPLOYEE_PASSWORD)
            dialog.confirm_password.setText(NEW_EMPLOYEE_PASSWORD)
            dialog.confirm_username.setText("wrong_employee")
            dialog.submit_button.click()
            self.assertTrue(dialog.isVisible())
            self.assertEqual(self.request("/api/me", token=alice["token"])[0], 200)
            dialog.confirm_username.setText("counter_alice")
            dialog.submit_button.click()
            wait_for(lambda: not dialog.isVisible() and not window._refreshing, "employee password reset")
            self.assertIn("Password reset", window.status.text())
            self.assertFalse(dialog.admin_password.text())
            self.assertFalse(dialog.new_password.text())
            for account in (alice, alice_second):
                self.assertEqual(self.request("/api/me", token=account["token"])[0], 401)
            self.assertEqual(self.request("/api/auth/reset-password", method="POST", data={
                "username": "counter_alice", "recovery_code": alice["recovery_code"],
                "new_password": "old recovery must not work"})[0], 401)
            alice = employee("counter_alice", register=False, password=NEW_EMPLOYEE_PASSWORD)
            alice_second = employee("counter_alice", register=False, password=NEW_EMPLOYEE_PASSWORD)
            history = self.request("/api/messages?peer_id=" + str(alice["user"]["id"]),
                                   token=ordinary_admin["token"])[1]
            self.assertEqual(history["messages"][0]["body"], "Preserve this pickup instruction")

            window.refresh()
            wait_for(lambda: not window._refreshing, "fresh employee sessions")
            window.users_table.selectRow(0)
            window.sign_out_button.click()
            dialog = window._action_dialog
            dialog.admin_password.setText(IT_PASSWORD)
            dialog.confirm_username.setText("counter_alice")
            dialog.submit_button.click()
            wait_for(lambda: not dialog.isVisible() and not window._refreshing, "employee remote sign-out")
            for account in (alice, alice_second):
                self.assertEqual(self.request("/api/me", token=account["token"])[0], 401)
            self.assertEqual(self.request("/api/me", token=ordinary_admin["token"])[0], 200)
            self.assertEqual(window.metrics["sessions"].text(), "1")
            self.assertGreaterEqual(window.audit_list.count(), 4)

            window.search.clear()
            for appearance in ("light", "dark"):
                buddy.apply_settings({"interface_appearance": appearance})
                for width, height in ((920, 720), (700, 520)):
                    window.resize(width, height)
                    APP.processEvents()
                    self.assertEqual(window.width(), width)
                    self.assertEqual(window.dashboard_page.horizontalScrollBar().maximum(), 0)
                    for flag in (Qt.WindowMinimizeButtonHint, Qt.WindowMaximizeButtonHint, Qt.WindowCloseButtonHint):
                        self.assertTrue(window.windowFlags() & flag)
            window.resize(920, 720)
            settings.values["interface_appearance"] = "light"
            window.configure()
            APP.processEvents()
            if os.environ.get("BUDDY_QA_DIR"):
                target = Path(os.environ["BUDDY_QA_DIR"])
                target.mkdir(parents=True, exist_ok=True)
                self.assertTrue(window.grab().save(str(target / "it-administration.png")))
            token = window.token
            window.home_button.click()
            self.assertFalse(window.token)
            self.assertFalse(window.isVisible())
            self.assertTrue(buddy.home.isVisible())
            wait_for(lambda: not window._logout_api._pending, "IT logout on Home navigation")
            self.assertEqual(self.request("/api/it/users", token=token)[0], 401)
            self.assertEqual(window.users_table.rowCount(), 0)
            self.assertEqual(window.audit_list.count(), 0)
            settings_text = settings.path.read_text(encoding="utf-8")
            for secret in (IT_PASSWORD, NEW_EMPLOYEE_PASSWORD, token):
                self.assertNotIn(secret, settings_text)

            # Close after the server issues a token but before its response arrives.
            # The client must allow that bounded response to finish and revoke it,
            # rather than aborting the callback and leaving a usable IT session.
            issued, release = threading.Event(), threading.Event()
            delayed_tokens = []
            real_login = self.app.state.it_admin.login

            def delayed_login(*args, **kwargs):
                session = real_login(*args, **kwargs)
                delayed_tokens.append(session["token"])
                issued.set()
                release.wait(5)
                return session

            with patch.object(self.app.state.it_admin, "login", side_effect=delayed_login):
                try:
                    window.show()
                    window.password_input.setText(IT_PASSWORD)
                    window.login_button.click()
                    wait_for(issued.is_set, "delayed server login")
                    window.close()
                    self.assertFalse(window.token)
                    release.set()
                    wait_for(lambda: not window.api._pending and not window._logout_api._pending,
                             "revocation of the delayed IT login")
                    self.assertEqual(self.request("/api/it/users", token=delayed_tokens[0])[0], 401)
                    self.assertIs(window.pages.currentWidget(), window.login_page)
                finally:
                    release.set()
        finally:
            if buddy is not None:
                buddy.shutdown()
                buddy.deleteLater()
            APP.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            for context in reversed(contexts):
                context.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
