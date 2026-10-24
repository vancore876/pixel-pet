"""IT account isolation, lifecycle and explicit employee action regressions."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton

from it_window import ITWindow, IDLE_LOCK_SECONDS
from settings import AppSettings

APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)


class ScriptedApi:
    """Keep cancelled callbacks callable to simulate replies racing with close."""
    def __init__(self):
        self.base_url, self.calls = "", []

    def request(self, method, path, body, callback, token=""):
        call = {"method": method, "path": path, "body": body, "callback": callback,
                "token": token, "done": False, "cancelled": False, "address": self.base_url}
        self.calls.append(call)
        return call

    def cancel(self, call):
        call["cancelled"] = True

    def cancel_all(self):
        for call in self.calls:
            if not call["done"]:
                self.cancel(call)

    def pending(self, path):
        return next(call for call in reversed(self.calls) if call["path"] == path and not call["done"])

    def finish(self, call, data=None, error="", status=200):
        call["done"] = True
        call["callback"](data, error, status)


def session(must_change=False, token="it_" + "a" * 40):
    return {"token": token, "username": "admin", "must_change_password": must_change,
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}


def employees():
    return {"users": [{"id": 4, "username": "alice", "created_at": "2026-01-01T12:00:00Z", "active_sessions": 2},
                      {"id": 7, "username": "bob", "created_at": "2026-01-02T12:00:00Z", "active_sessions": 0}]}


def overview():
    return {"users": 2, "sessions": 2, "messages": 3210, "inventory_items": 200,
            "audit": [{"created_at": "2026-01-02T12:00:00Z", "action": "employee_password_reset", "target_username": "alice"}]}


class ITWindowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = AppSettings(Path(self.temp.name) / "settings.json")
        self.settings.values["work_chat_server"] = "http://192.168.10.8:8765"
        self.settings.values["work_chat_username"] = "employee_account"
        self.original_settings = dict(self.settings.values)
        self.window = ITWindow(self.settings)
        self.api = self.window.api = ScriptedApi()
        self.logout_api = self.window._logout_api = ScriptedApi()
        self.window.show()
        APP.processEvents()

    def tearDown(self):
        self.window.shutdown()
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()
        self.temp.cleanup()

    def login(self, must_change=False):
        self.window.password_input.setText("admin123" if must_change else "an administrator phrase")
        self.window.login_button.click()
        call = self.api.pending("/api/it/login")
        self.api.finish(call, session(must_change))
        return call

    def dashboard(self):
        self.login()
        self.api.finish(self.api.pending("/api/it/overview"), overview())
        self.api.finish(self.api.pending("/api/it/users"), employees())

    def test_real_settings_constructs_distinct_session_without_saving_credentials(self):
        self.assertEqual(self.window.username_input.text(), "admin")
        self.assertEqual(self.window.password_input.text(), "")
        self.assertEqual(self.window.server_input.text(), self.settings["work_chat_server"])
        call = self.login()
        self.assertEqual(call["token"], "")
        self.assertEqual(self.window.password_input.text(), "")
        self.assertTrue(self.window.token.startswith("it_"))
        self.assertEqual(self.settings.values, self.original_settings)
        self.assertFalse((Path(self.temp.name) / "settings.json").exists())

    def test_setup_requires_rotation_before_dashboard_and_clears_password_inputs(self):
        self.login(True)
        self.assertIs(self.window.pages.currentWidget(), self.window.password_page)
        self.assertEqual([call["path"] for call in self.api.calls], ["/api/it/login"])
        self.assertFalse(self.window.password_back_button.isVisible())
        self.window.current_password.setText("admin123")
        self.window.new_password.setText("too short")
        self.window.confirm_password.setText("too short")
        self.window.change_password_button.click()
        self.assertIn("12–128", self.window.status.text())
        self.assertEqual(len(self.api.calls), 1)
        self.window.new_password.setText("new administrator password")
        self.window.confirm_password.setText("new administrator password")
        QTest.keyClick(self.window.confirm_password, Qt.Key_Return)
        call = self.api.pending("/api/it/password")
        self.assertEqual(call["body"], {"current_password": "admin123", "new_password": "new administrator password"})
        self.assertEqual(self.window.current_password.text() + self.window.new_password.text() + self.window.confirm_password.text(), "")
        self.api.finish(call, session(token="it_" + "b" * 40))
        self.assertIs(self.window.pages.currentWidget(), self.window.dashboard_page)
        self.assertEqual(self.window.token, "it_" + "b" * 40)
        self.assertEqual(self.api.pending("/api/it/users")["token"], self.window.token)

    def test_close_discards_late_login_and_revokes_issued_session(self):
        self.window.password_input.setText("admin123")
        self.window.login_button.click()
        call = self.api.pending("/api/it/login")
        self.window.close()
        self.assertFalse(call["cancelled"])
        self.api.finish(call, session())
        self.assertEqual(self.window.token, "")
        self.assertEqual(self.window.password_input.text(), "")
        revoke = self.logout_api.pending("/api/it/logout")
        self.assertEqual(revoke["token"], session()["token"])
        self.assertEqual(revoke["address"], "http://192.168.10.8:8765")
        self.assertIs(self.window.pages.currentWidget(), self.window.login_page)

    def test_new_login_does_not_cancel_old_session_response_revocation(self):
        self.window.password_input.setText("first administrator phrase")
        self.window.login_button.click()
        old = self.api.pending("/api/it/login")
        self.window.server_input.setText("192.168.10.9:8765")
        self.window.password_input.setText("second administrator phrase")
        self.window.login_button.click()
        new = self.api.pending("/api/it/login")
        self.assertFalse(old["cancelled"])
        self.api.finish(new, session(token="it_" + "b" * 40))
        self.api.finish(old, session())
        self.assertEqual(self.window.token, "it_" + "b" * 40)
        self.assertEqual(self.logout_api.pending("/api/it/logout")["address"], "http://192.168.10.8:8765")
        self.assertEqual(self.logout_api.pending("/api/it/logout")["token"], "it_" + "a" * 40)

    def test_address_change_clears_password_and_old_authenticated_data(self):
        self.window.password_input.setText("private credential")
        self.window.server_input.setText("192.168.10.9:8765")
        self.assertEqual(self.window.password_input.text(), "")
        self.dashboard()
        self.window.refresh()
        late = self.api.pending("/api/it/users")
        self.window.server_input.setText("192.168.10.10:8765")
        self.assertEqual(self.window.token, "")
        self.assertEqual(self.window.users_table.rowCount(), 0)
        self.assertEqual(self.window.audit_list.count(), 0)
        self.assertEqual(self.logout_api.pending("/api/it/logout")["address"], "http://192.168.10.9:8765")
        self.api.finish(late, employees())
        self.assertEqual(self.window.users_table.rowCount(), 0)

    def test_idle_timeout_uses_only_it_activity_and_expiry_also_locks(self):
        self.dashboard()
        self.window._last_activity = time.monotonic() - IDLE_LOCK_SECONDS - 1
        other = QLineEdit()
        self.window.eventFilter(other, QKeyEvent(QEvent.KeyPress, Qt.Key_A, Qt.NoModifier))
        self.window._check_timeout()
        self.assertEqual(self.window.token, "")
        self.assertIn("15 minutes", self.window.status.text())
        self.dashboard()
        self.window._last_activity = time.monotonic() - IDLE_LOCK_SECONDS - 1
        self.window.eventFilter(self.window.search, QKeyEvent(QEvent.KeyPress, Qt.Key_A, Qt.NoModifier))
        self.window._check_timeout()
        self.assertTrue(self.window.token)
        self.window._expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.window._check_timeout()
        self.assertEqual(self.window.token, "")
        self.assertIn("expired", self.window.status.text())
        other.deleteLater()

    def test_dashboard_formats_counts_filters_employees_and_renders_plain_audit(self):
        self.dashboard()
        self.assertEqual(self.window.metrics["messages"].text(), "3,210")
        self.assertEqual(self.window.users_table.rowCount(), 2)
        self.window.users_table.selectRow(0)
        self.assertTrue(self.window.reset_button.isEnabled())
        self.window.search.setText("BOB")
        self.assertEqual(self.window.users_table.rowCount(), 1)
        self.assertEqual(self.window.users_table.item(0, 0).text(), "bob")
        data = overview()
        data["audit"][0]["target_username"] = "<b>private</b>"
        self.window._overview(data)
        self.assertIn("<b>private</b>", self.window.audit_list.item(0).text())
        self.assertEqual(self.window.status.textFormat(), Qt.PlainText)

    def test_enter_on_employee_table_does_not_trigger_account_action(self):
        self.dashboard()
        self.window.users_table.selectRow(0)
        self.window.users_table.setFocus()
        previous = len(self.api.calls)
        QTest.keyClick(self.window.users_table, Qt.Key_Return)
        self.assertEqual(len(self.api.calls), previous)
        self.assertIsNone(self.window._action_dialog)
        self.assertTrue(all(not button.autoDefault() and not button.isDefault()
                            for button in self.window.findChildren(QPushButton)))

    def test_employee_reset_requires_exact_confirmation_and_explicit_submit(self):
        self.dashboard()
        self.window.users_table.selectRow(0)
        self.window.reset_button.click()
        dialog = self.window._action_dialog
        dialog.admin_password.setText("admin current password")
        dialog.new_password.setText("employee replacement phrase")
        dialog.confirm_password.setText("employee replacement phrase")
        dialog.confirm_username.setText("Alice")
        dialog.submit_button.click()
        self.assertIn("exactly", dialog.status.text())
        dialog.confirm_username.setText("alice")
        previous = len(self.api.calls)
        QTest.keyClick(dialog.confirm_username, Qt.Key_Return)
        self.assertEqual(len(self.api.calls), previous)
        dialog.submit_button.click()
        call = self.api.pending("/api/it/users/4/reset-password")
        self.assertEqual(call["body"], {"admin_password": "admin current password", "new_password": "employee replacement phrase", "confirm_username": "alice"})
        self.assertEqual(dialog.admin_password.text() + dialog.new_password.text() + dialog.confirm_password.text(), "")
        self.assertFalse(dialog.submit_button.isEnabled())
        self.api.finish(call, None, "", 204)
        self.assertFalse(dialog.isVisible())
        self.assertIn("Password reset for alice", self.window.status.text())
        self.assertEqual(self.api.pending("/api/it/users")["token"], self.window.token)

    def test_employee_signout_cancel_and_stale_completion_do_not_reopen_or_refresh(self):
        self.dashboard()
        self.window.users_table.selectRow(1)
        self.window.sign_out_button.click()
        dialog = self.window._action_dialog
        dialog.admin_password.setText("admin current password")
        dialog.confirm_username.setText("bob")
        dialog.submit_button.click()
        call = self.api.pending("/api/it/users/7/sign-out")
        self.assertEqual(call["body"], {"admin_password": "admin current password", "confirm_username": "bob"})
        dialog.reject()
        self.assertTrue(call["cancelled"])
        count = len(self.api.calls)
        self.api.finish(call, None, "", 204)
        self.assertEqual(len(self.api.calls), count)
        self.assertFalse(dialog.isVisible())
        self.assertEqual(dialog.confirm_username.text(), "")

    def test_wrong_admin_password_revokes_session_and_clears_dialog(self):
        self.dashboard()
        self.window.users_table.selectRow(0)
        self.window.sign_out_button.click()
        dialog = self.window._action_dialog
        dialog.admin_password.setText("wrong password")
        dialog.confirm_username.setText("alice")
        dialog.submit_button.click()
        call = self.api.pending("/api/it/users/4/sign-out")
        token = self.window.token
        self.api.finish(call, None, "Incorrect IT password", 401)
        self.assertEqual(self.window.token, "")
        self.assertEqual(self.logout_api.pending("/api/it/logout")["token"], token)
        self.assertFalse(dialog.isVisible())
        self.assertEqual(dialog.admin_password.text(), "")
        self.assertEqual(self.window.users_table.rowCount(), 0)

    def test_invalid_session_and_malformed_rows_are_rejected_without_partial_data(self):
        self.window.password_input.setText("admin123")
        self.window.login_button.click()
        invalid = session()
        invalid["token"] = "token\nheader" * 3
        self.api.finish(self.api.pending("/api/it/login"), invalid)
        self.assertEqual(self.window.token, "")
        self.assertIn("invalid IT session", self.window.status.text())
        self.dashboard()
        users = employees()
        users["users"].append({"id": 9, "username": "<b>bad</b>", "active_sessions": 0, "created_at": "2026-01-01T00:00:00Z"})
        with self.assertRaises(ValueError):
            self.window._set_users(users)
        self.assertEqual(self.window.users_table.rowCount(), 2)
        data = overview()
        data["messages"] = True
        with self.assertRaises(ValueError):
            self.window._overview(data)
        self.assertEqual(self.window.metrics["messages"].text(), "3,210")

    def test_missing_server_feature_explains_update_and_login_retries(self):
        self.window.password_input.setText("admin123")
        self.window.login_button.click()
        self.api.finish(self.api.pending("/api/it/login"), None, "Not Found", 404)
        self.assertIn('IT service is missing', self.window.status.text())
        self.assertIn('HTTP 404', self.window.status.text())
        self.assertFalse(self.window.token)
        self.assertTrue(self.window.login_button.isEnabled())

    def test_home_theme_and_shutdown_preserve_native_controls_and_lock_session(self):
        self.dashboard()
        self.window.configure()
        self.assertTrue(self.window.isVisible())
        flags = self.window.windowFlags()
        for flag in (Qt.WindowMinimizeButtonHint, Qt.WindowMaximizeButtonHint, Qt.WindowCloseButtonHint):
            self.assertTrue(flags & flag)
        home = []
        self.window.home_requested.connect(lambda: home.append(True))
        self.window.home_button.click()
        self.assertEqual(home, [True])
        self.assertEqual(self.window.token, "")
        self.assertFalse(self.window.isVisible())
        self.window.show()
        self.dashboard()
        self.window.shutdown()
        self.assertEqual(self.window.token, "")
        self.assertFalse(self.window.isVisible())
        self.assertFalse(self.window._idle_timer.isActive())
        self.assertFalse(self.window.login_button.isEnabled())
        self.assertFalse(self.logout_api.pending("/api/it/logout")["cancelled"])


if __name__ == "__main__":
    unittest.main()
