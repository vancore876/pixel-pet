"""Exercise Home, cross-conversation office popups, account recovery and stock import.

Run with the desktop interpreter and optional requirements-server.txt installed.
Every account, database, workbook and notification watermark is disposable.
Set BUDDY_QA_DIR to retain the Home and stock review screenshots.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["GROQ_API_KEY"] = ""
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt, QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from main import BuddyApp
from settings import AppSettings
from work_chat import WorkChatWindow
import test_work_server as server_tests

APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)
PASSWORD = "disposable office passphrase"


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
class OfficeUpdateIntegrationTests(unittest.TestCase):
    setUpClass = classmethod(server_tests.WorkServerHTTPTests.setUpClass.__func__)
    tearDownClass = classmethod(server_tests.WorkServerHTTPTests.tearDownClass.__func__)
    request = server_tests.WorkServerHTTPTests.request

    def setUp(self):
        self.root = Path(self.directory.name)
        self.windows = []
        self.buddy = None
        self.patches = [
            patch("main.CredentialStore", return_value=NoCredentials()),
            patch("config.team_data_directory", return_value=self.root / "local-profile"),
            patch("work_notifications.team_data_directory", return_value=self.root / "local-profile"),
            patch.dict(os.environ, {"LOCALAPPDATA": str(self.root / "local-appdata")}),
        ]
        for context in self.patches:
            context.start()
        self.settings = AppSettings(self.root / "bob" / "settings.json")
        self.settings.save({"desktop_enabled": False, "mouse_mode": "off", "ai_autonomy": False,
            "memory_ai": False, "business_greetings": False, "home_on_start": False,
            "semantic_memory_enabled": False, "pet_enabled": False, "team_sync_enabled": False,
            "work_chat_notifications": True, "work_chat_reminder_minutes": 30})
        self.buddy = BuddyApp(APP, self.settings, show_tray=False)
        self.buddy.note_service.stop()
        self.buddy.overlay.hide()
        self.buddy.pet.hide()

    def tearDown(self):
        for window in self.windows:
            window.shutdown()
            window.deleteLater()
        if self.buddy is not None:
            self.buddy.shutdown()
            self.buddy.deleteLater()
        APP.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        for context in reversed(self.patches):
            context.stop()

    def authenticate(self, window, username, password=PASSWORD, *, register=True):
        window.show()
        window.server.setText(self.address)
        window.username.setText(username)
        window.signup.setChecked(register)
        window.password.setText(password)
        window.confirm_password.setText(password)
        window.authenticate()
        wait_for(lambda: not window._auth_busy, username + " authentication")
        self.assertTrue(window.token, window.status.text())
        wait_for(lambda: not window._users_pending and not window._messages_pending, username + " initial history")
        code = window.security_dialog.code.text() if register and window.security_dialog is not None else ""
        if window.security_dialog is not None:
            window.security_dialog.close()
        return code

    def account(self, username):
        window = WorkChatWindow(AppSettings(self.root / username / "settings.json"))
        self.windows.append(window)
        code = self.authenticate(window, username)
        return window, code

    def choose(self, window, peer):
        window.show()
        window.refresh_users()
        wait_for(lambda: not window._users_pending, "coworker roster")
        for index in range(window.conversations.count()):
            if window.conversations.item(index).data(Qt.UserRole) == peer:
                window.conversations.setCurrentRow(index)
                wait_for(lambda: not window._messages_pending, "selected conversation")
                self.assertEqual(window._peer_id, peer)
                return
        self.fail("The real server did not return the selected coworker.")

    def send(self, window, body, peer=None):
        self.choose(window, peer)
        window.composer.setPlainText(body)
        window.composer.setFocus()
        QTest.keyClick(window.composer, Qt.Key_Return)
        wait_for(lambda: not window._send_pending and not window.composer.toPlainText(), "Enter sending " + body)
        wait_for(lambda: any(row["body"] == body for row in window._messages.values()), "posted message response")
        return next(row["id"] for row in window._messages.values() if row["body"] == body)

    def receive(self, identifier, body, *, reminder=False):
        popup, service = self.buddy.work_popup, self.buddy.work_notifications
        wait_for(lambda: popup.isVisible() and popup.identifier == str(identifier), "background message popup " + body)
        self.assertEqual(popup.body.toPlainText(), body)
        self.assertEqual(popup.notification["notification_kind"], "reminder" if reminder else "received")
        self.assertTrue(service.notification_active(popup.notification))
        self.assertIn(identifier, service._reminders)

    def screenshot(self, window, name):
        directory = Path(os.environ.get("BUDDY_QA_DIR", self.root / "screenshots"))
        directory.mkdir(parents=True, exist_ok=True)
        self.assertTrue(window.grab().save(str(directory / name)))

    def test_real_office_updates(self):
        buddy = self.buddy
        alice, alice_code = self.account("office_alice")
        charlie, _ = self.account("office_charlie")
        bob = buddy.work_chat
        self.authenticate(bob, "office_bob")
        notifications = buddy.work_notifications
        notifications.timer.setInterval(50)
        wait_for(lambda: notifications._cursor is not None and not notifications._request_pending, "notification baseline")

        self.check_home_metrics()
        self.check_background_messages(alice, bob, charlie)
        self.check_visible_cross_conversation_messages(alice, bob, charlie)
        self.check_notification_gates(alice, bob)
        self.check_inventory_import(bob)
        self.check_recovery_and_forget(alice, alice_code)
        self.check_permanent_account_deletion(bob, charlie)

        self.assertFalse(notifications._token)
        self.assertFalse(buddy.work_popup.isVisible())
        self.assertEqual(buddy.work_popup.notification, {})
        self.assertEqual(buddy.inventory.catalog, [])
        self.assertEqual(buddy.inventory.context("A04-001"), [])
        self.assertFalse(buddy.inventory.can_write)
        buddy.shutdown()
        self.assertFalse(buddy.thread.isRunning())
        self.assertFalse(notifications.timer.isActive())

    def check_home_metrics(self):
        window = self.buddy.home
        window.show()
        summary = {"open_orders": 123456789012345, "ready_orders": 12345,
                   "urgent_orders": 1234, "balance_cents": 1_999_800_000_000_000_000}
        with patch("home_window.dashboard_summary", return_value=summary):
            for appearance in ("light", "dark"):
                self.buddy.apply_settings({"interface_appearance": appearance})
                window.resize(800, 600)
                window.refresh()
                APP.processEvents()
                APP.processEvents()
                for label in window.metric_labels.values():
                    self.assertGreaterEqual(label.height(), label.fontMetrics().height())
                    self.assertGreaterEqual(label.width(), label.fontMetrics().horizontalAdvance(label.text()))
                self.assertEqual(window.scroll.horizontalScrollBar().maximum(), 0)
                window.scroll.ensureWidgetVisible(window.metric_labels["balance_cents"])
                APP.processEvents()
                self.screenshot(window, "office-home-large-values-" + appearance + ".png")
        window.refresh()
        window.resize(1140, 790)
        window.scroll.verticalScrollBar().setValue(0)
        APP.processEvents()
        APP.processEvents()
        self.screenshot(window, "office-home-readable-metrics.png")
        window.hide()

    def check_background_messages(self, alice, bob, charlie):
        buddy, popup = self.buddy, self.buddy.work_popup
        service = buddy.work_notifications
        alice_id, bob_id, charlie_id = alice.user["id"], bob.user["id"], charlie.user["id"]
        self.choose(bob, charlie_id)
        bob.composer.setPlainText("Keep this unrelated private draft")
        bob.hide()

        self.assertFalse(bob.poll_timer.isActive(), "Background delivery must be independent of visible chat polling")

        direct = self.send(alice, "Personal popup while Bob is hidden", bob_id)
        self.receive(direct, "Personal popup while Bob is hidden")
        self.assertEqual(popup.heading.text(), "New personal message")
        popup.open_button.click()
        wait_for(lambda: bob.isVisible() and bob._peer_id == alice_id, "popup direct conversation routing")
        self.assertEqual(bob._drafts[charlie_id], "Keep this unrelated private draft")
        self.assertIn(direct, service._reminders, "Opening is not acknowledging")
        self.choose(bob, charlie_id)
        self.assertEqual(bob.composer.toPlainText(), "Keep this unrelated private draft")
        bob.hide()

        # Several sends while one card is visible must each receive a card.
        team = self.send(alice, "Team popup while Bob is hidden")
        self.receive(team, "Team popup while Bob is hidden")
        second = self.send(alice, "Second personal waits for its own popup", bob_id)
        third = self.send(charlie, "Third team waits for its own popup")
        unrelated = self.send(alice, "Only Charlie must see this", charlie_id)
        self.assertEqual(popup.identifier, str(team))
        popup.open_button.click()
        wait_for(lambda: bob.isVisible() and bob._peer_id is None, "popup Team Room routing")
        self.receive(second, "Second personal waits for its own popup")
        popup.acknowledge_button.click()
        self.receive(third, "Third team waits for its own popup")
        popup.acknowledge_button.click()
        self.assertNotIn(second, service._reminders)
        self.assertNotIn(third, service._reminders)
        self.assertNotIn(unrelated, service._reminders)

        # The reminder is a real accepted card; dismissing/reading did not clear it.
        service._reminders[direct]["_due"] = time.time() - 1
        self.receive(direct, "Personal popup while Bob is hidden", reminder=True)
        popup.acknowledge_button.click()
        self.assertNotIn(direct, service._reminders)

        buddy.apply_settings({"quiet_mode": True})
        delayed = self.send(alice, "Quiet mode keeps this pending", bob_id)
        wait_for(lambda: any(row["id"] == delayed for row in service._queue), "quiet-mode pending delivery")
        self.assertFalse(popup.isVisible())
        buddy.apply_settings({"quiet_mode": False})
        self.receive(delayed, "Quiet mode keeps this pending")
        popup.acknowledge_button.click()

        # Neither our own sends nor somebody else's DM can produce a Bob card.
        bob.composer.clear()
        own = self.send(bob, "Bob's own outgoing message")
        wait_for(lambda: not service._request_pending, "completed notification polling")
        service.pump()
        wait_for(lambda: not service._request_pending, "empty incoming feed")
        self.assertFalse(popup.isVisible())
        self.assertNotIn(own, service._reminders)
        self.assertNotIn(unrelated, service._reminders)
        bob.hide()

    def check_visible_cross_conversation_messages(self, alice, bob, charlie):
        """BuddyApp must notify for other chats without moving the current one."""
        buddy, popup, service = self.buddy, self.buddy.work_popup, self.buddy.work_notifications
        alice_id, bob_id, charlie_id = alice.user["id"], bob.user["id"], charlie.user["id"]
        self.choose(bob, alice_id)
        draft = "Alice's unfinished reply stays here\nincluding this second line"
        bob.composer.setPlainText(draft)
        service.timer.stop()
        try:
            direct_body = "Charlie personal popup while Bob reads Alice: <b>keep literal</b>"
            direct = self.send(charlie, direct_body, bob_id)
            bob.raise_()
            bob.activateWindow()
            bob.composer.setFocus()
            APP.processEvents()
            active_window = APP.activeWindow()
            self.assertIs(active_window, bob)
            service.pump()
            self.receive(direct, direct_body)
            self.assertEqual(bob._peer_id, alice_id)
            self.assertEqual(bob.composer.toPlainText(), draft)
            self.assertNotIn(direct_body, bob.transcript.toPlainText())
            self.assertTrue(popup.testAttribute(Qt.WA_ShowWithoutActivating))
            self.assertTrue(popup.windowFlags() & Qt.WindowDoesNotAcceptFocus)
            # Offscreen/minimal Qt synthesize activation for all shown windows.
            if APP.platformName() not in ("offscreen", "minimal"):
                self.assertIs(APP.activeWindow(), active_window, "A popup must not steal the employee's active window")
            self.assertTrue(bob.isVisible())

            # The incoming Team card waits behind this personal card, then
            # appears when it is dismissed without selecting another chat.
            team_body = "Team popup while Bob keeps working in Alice's conversation"
            team = self.send(charlie, team_body)
            service.pump()
            wait_for(lambda: not service._request_pending, "queued cross-conversation Team message")
            self.assertEqual(popup.identifier, str(direct))
            self.assertTrue(any(row["id"] == team for row in service._queue))
            self.assertEqual(bob._peer_id, alice_id)
            self.assertEqual(bob.composer.toPlainText(), draft)
            popup.dismiss_button.click()
            self.assertIn(direct, service._reminders, "Dismissing the card must leave its reminder enabled")
            service.pump()
            self.receive(team, team_body)
            self.assertEqual(popup.heading.text(), "New team room message")
            self.assertEqual(bob._peer_id, alice_id)
            self.assertEqual(bob.composer.toPlainText(), draft)
            popup.open_button.click()
            wait_for(lambda: bob._peer_id is None and not bob._messages_pending, "explicit Team card Open")
            self.assertEqual(bob._drafts[alice_id], draft)
            self.assertIn(team, bob._messages)
            self.choose(bob, alice_id)
            self.assertEqual(bob.composer.toPlainText(), draft)

            # The stored DM target must still work when that message returns
            # as a reminder, independently of the currently selected chat.
            service._reminders[direct]["_due"] = time.time() - 1
            service.pump()
            self.receive(direct, direct_body, reminder=True)
            self.assertEqual(bob._peer_id, alice_id)
            popup.open_button.click()
            wait_for(lambda: bob._peer_id == charlie_id and not bob._messages_pending,
                     "explicit other-employee DM Open")
            self.assertEqual(bob._drafts[alice_id], draft)
            self.assertIn(direct_body, bob.transcript.toPlainText())
            service.acknowledge(direct)
            service.acknowledge(team)
        finally:
            service.timer.start()

    def check_notification_gates(self, alice, bob):
        """Visible notebook/shared-folder cards must defer Work Chat delivery."""
        buddy, popup, service = self.buddy, self.buddy.work_popup, self.buddy.work_notifications
        service.timer.stop()
        try:
            for blocking_popup, source in ((buddy.note_popup, "notebook"), (buddy.team_popup, "shared-folder team")):
                blocking_popup.show()
                APP.processEvents()
                self.assertFalse(buddy.work_can_notify())
                self.assertFalse(buddy.team_can_notify())
                self.assertFalse(buddy.note_service.can_notify())
                body = "Work Chat waits for the " + source + " card"
                identifier = self.send(alice, body, bob.user["id"])
                service.pump()
                wait_for(lambda: not service._request_pending and any(row["id"] == identifier for row in service._queue),
                         source + " card retaining the Work Chat queue")
                self.assertFalse(popup.isVisible())
                self.assertNotIn(identifier, service._reminders, "Deferred cards must not count as presented")
                blocking_popup.hide()
                service.pump()
                self.receive(identifier, body)
                self.assertFalse(buddy.work_can_notify())
                self.assertFalse(buddy.team_can_notify())
                self.assertFalse(buddy.note_service.can_notify())
                popup.acknowledge_button.click()
                self.assertNotIn(identifier, service._reminders)
            self.assertTrue(buddy.work_can_notify())
            self.assertTrue(buddy.team_can_notify())
            self.assertTrue(buddy.note_service.can_notify())
        finally:
            buddy.note_popup.hide()
            buddy.team_popup.hide()
            popup.dismiss()
            service.timer.start()
    def check_inventory_import(self, bob):
        # InventoryWindow is deliberately exercised through the coordinator so
        # missing navigation/session wiring cannot pass as an isolated UI test.
        self.assertTrue(hasattr(self.buddy, "inventory"), "Inventory is not wired into BuddyApp yet")
        from openpyxl import Workbook
        service, window = self.buddy.inventory, self.buddy.inventory_window
        self.buddy.home.nav_buttons["inventory"].click()
        self.assertTrue(window.isVisible())
        wait_for(lambda: service.can_write and not service._syncing, "authenticated shared stock snapshot")
        self.assertEqual(service.summary["item_count"], 0)
        workbook_path = self.root / "auto-parts-stock.xlsx"
        workbook = Workbook()
        workbook.active.title = "Read me"
        workbook.active.append(["Choose the Stock worksheet"])
        sheet = workbook.create_sheet("Stock")
        sheet.append(["Part number", "Description", "Quantity on hand", "Location", "Reorder level", "Restock quantity", "Supplier"])
        sheet.append(["A04-001", "Front brake pads", 2, "Aisle 2 / Shelf B / Bin 7", 5, 12, "Island Parts"])
        sheet.append(["N88-204", "Oil filter", 12, "Aisle 4 / Shelf A / Bin 2", 4, 24, "Island Parts"])
        workbook.save(workbook_path)
        workbook.close()
        window.tabs.setCurrentIndex(1)
        with patch("inventory_window.QFileDialog.getOpenFileName", return_value=(str(workbook_path), "Excel (*.xlsx)")):
            window.choose_button.click()
        wait_for(lambda: service.preview is not None and not service.busy, "real XLSX header preview")
        window.sheet.setCurrentText("Stock")
        wait_for(lambda: service.preview is not None and service.preview.sheet == "Stock" and not service.busy,
                 "selected Stock worksheet")
        self.assertTrue(window.review_button.isEnabled())
        self.assertFalse(window.import_button.isEnabled())
        window.review_button.click()
        wait_for(lambda: service.plan is not None and not service.busy, "mapped spreadsheet review")
        self.assertTrue(service.plan.valid, service.plan.errors)
        self.assertEqual(len(service.plan.rows), 2)
        self.assertTrue(window.import_button.isEnabled())
        self.assertEqual(self.request("/api/inventory/summary", token=bob.token)[1]["item_count"], 0,
                         "Reading and reviewing the spreadsheet must leave shared stock unchanged")
        self.screenshot(window, "office-stock-explicit-import-review.png")
        window.import_button.click()
        wait_for(lambda: not service.busy and not service._syncing and service.summary.get("item_count") == 2,
                 "explicitly committed shared inventory")
        self.assertEqual(service.summary["total_quantity"], 14)
        self.assertEqual(service.summary["low_stock_count"], 1)
        self.assertEqual(window.metrics["item_count"].text(), "2")
        self.assertEqual(window.metrics["low_stock_count"].text(), "1")

        window.search.setText("Aisle 2")
        QTest.keyClick(window.search, Qt.Key_Return)
        wait_for(lambda: window.stock_table.rowCount() == 1, "shelf and bin lookup")
        self.assertEqual(window.stock_table.item(0, 0).text(), "A04-001")
        self.assertEqual(window.stock_table.item(0, 3).text(), "Aisle 2 / Shelf B / Bin 7")
        window.search.clear()
        window.low_stock.setChecked(True)
        wait_for(lambda: window.stock_table.rowCount() == 1, "restock list")
        self.assertEqual(window.stock_table.item(0, 0).text(), "A04-001")
        self.assertEqual(window.stock_table.item(0, 5).text(), "12")
        self.screenshot(window, "office-stock-restock-and-location.png")

        other_token = self.windows[0].token
        status, payload, _ = self.request("/api/inventory?query=A04-001", token=other_token)
        self.assertEqual(status, 200)
        self.assertEqual(payload["items"][0]["location"], "Aisle 2 / Shelf B / Bin 7")
        self.assertEqual(payload["items"][0]["quantity"], 2)
        self.assertEqual(self.buddy.inventory_context("A04-001"), {}, "Work stock needs separate AI consent")
        self.buddy.apply_ai_preferences({"inventory_share_with_ai": True})
        context = self.buddy.inventory_context("A04-001")
        self.assertEqual(context["items"][0]["location"], "Aisle 2 / Shelf B / Bin 7")
        self.assertFalse(context["live_stock_count"], "The future counter connection is not live yet")
        self.buddy.apply_ai_preferences({"inventory_share_with_ai": False})

        window.stock_table.selectRow(0)
        self.assertTrue(window.save_button.isEnabled())
        window.editors["quantity"].setValue(15)
        window.editors["location"].setText("Aisle 2 / Shelf C / Bin 1")
        window.save_button.click()
        wait_for(lambda: not service.busy and not service._syncing and service.summary.get("total_quantity") == 27,
                 "explicit manual restock and shelf update")
        self.assertEqual(service.summary["low_stock_count"], 0)
        self.assertEqual(window.stock_table.rowCount(), 0)
        saved = self.request("/api/inventory/items/A04-001", token=other_token)[1]
        self.assertEqual(saved["quantity"], 15)
        self.assertEqual(saved["location"], "Aisle 2 / Shelf C / Bin 1")
        window.hide()

    def check_recovery_and_forget(self, alice, original_code):
        old_token = alice.token
        username = alice.user["username"]
        reset = WorkChatWindow(AppSettings(self.root / "reset-client" / "settings.json"))
        self.windows.append(reset)
        reset.show()
        reset.server.setText(self.address)
        reset.username.setText(username)
        reset.forgot_button.click()
        self.assertTrue(reset._recovery_mode)
        new_password = "replacement office passphrase"
        reset.recovery_code.setText(original_code)
        reset.password.setText(new_password)
        reset.confirm_password.setText(new_password)
        reset.auth_button.click()
        wait_for(lambda: not reset._auth_busy, "real forgotten password reset")
        self.assertIn("Password reset", reset.status.text())
        self.assertFalse(reset.token)
        self.assertTrue(reset.security_dialog is not None)
        replacement_code = reset.security_dialog.code.text()
        self.assertTrue(replacement_code)
        self.assertNotEqual(replacement_code, original_code)
        reset.security_dialog.close()
        self.assertEqual(reset.security_dialog.code.text(), "")
        self.assertEqual(self.request("/api/me", token=old_token)[0], 401)
        self.assertEqual(self.request("/api/auth/login", method="POST",
            data={"username": username, "password": PASSWORD})[0], 401)
        self.authenticate(reset, username, new_password, register=False)
        with patch("work_chat.QMessageBox.question", return_value=QMessageBox.Yes):
            self.assertTrue(reset.forget_saved_login())
        self.assertEqual(reset.server.text(), "")
        self.assertEqual(reset.username.text(), "")
        self.assertFalse(reset.token)
        self.assertEqual(self.request("/api/auth/login", method="POST",
            data={"username": username, "password": new_password})[0], 200,
            "Forgetting this PC must preserve the real office account")
        for path in self.root.rglob("*.json"):
            text = path.read_text(encoding="utf-8")
            for secret in (PASSWORD, new_password, original_code, replacement_code, old_token):
                self.assertNotIn(secret, text, "A login secret leaked into portable settings")

    def check_permanent_account_deletion(self, bob, charlie):
        from test_work_business import entry

        buddy = self.buddy
        username, identifier, token = bob.user["username"], bob.user["id"], bob.token
        second_pc = WorkChatWindow(AppSettings(self.root / "bob-second-pc" / "settings.json"))
        self.windows.append(second_pc)
        self.authenticate(second_pc, username, register=False)
        second_token = second_pc.token
        self.assertNotEqual(second_token, token)
        company_entry = entry(title="Company order survives employee account deletion")
        status, saved, _ = self.request("/api/business/" + company_entry["id"], method="PUT", token=token,
            data={"expected_revision": 0, "entry": company_entry})
        self.assertEqual(status, 200, saved)
        self.assertEqual(saved["updated_by_username"], username)
        team_id = self.send(bob, "Company team history survives account deletion")
        direct_id = self.send(bob, "Keep Charlie's company conversation history", charlie.user["id"])

        # Start with real, visible account-scoped surfaces. A successful delete
        # must clear the receiving PC as well as revoke its tokens on the server.
        buddy.show_inventory()
        buddy.inventory_window.low_stock.setChecked(False)
        wait_for(lambda: buddy.inventory_window.stock_table.rowCount() == 2, "visible stock before account deletion")
        incoming = self.send(charlie, "This notification must clear when Bob deletes his account", identifier)
        self.receive(incoming, "This notification must clear when Bob deletes his account")
        buddy.apply_ai_preferences({"inventory_share_with_ai": True})
        self.assertTrue(buddy.inventory_context("A04-001")["items"])

        bob.show_account_security()
        dialog = bob.security_dialog
        dialog.delete_button.click()
        self.assertTrue(dialog.delete_panel.isVisible())
        self.assertEqual(self.request("/api/me", token=token)[0], 200,
                         "Opening the deletion panel must leave the account intact")
        dialog.delete_username.setText("a_different_username")
        dialog.delete_password.setText(PASSWORD)
        dialog.delete_confirm_button.click()
        APP.processEvents()
        self.assertTrue(bob.token)
        self.assertIn("exact", dialog.status.text())
        self.assertEqual(self.request("/api/me", token=token)[0], 200)
        dialog.delete_username.setText(username)
        QTest.keyClick(dialog.delete_password, Qt.Key_Return)
        APP.processEvents()
        self.assertEqual(self.request("/api/me", token=token)[0], 200,
                         "Enter in the password field must not permanently delete an account")

        # Populate the pending AI turn from the real consented inventory cache;
        # no model request or credential is needed to verify account cleanup.
        buddy.chat.history = [{"role": "assistant", "content": "A04-001 is on Aisle 2 / Shelf C / Bin 1"}]
        buddy.chat.pending = [{"role": "system", "content": str(buddy.inventory_context("A04-001"))}]
        buddy.chat.semantic_pending = "Where is A04-001?"
        buddy.chat.semantic_loading = True
        with patch.object(buddy.chat, "cancel_request", wraps=buddy.chat.cancel_request) as cancelled:
            dialog.delete_confirm_button.click()
            wait_for(lambda: not bob.token, "confirmed permanent server account deletion")
            self.assertTrue(cancelled.called)
        self.assertIsNone(bob.user)
        self.assertIn("permanently deleted", bob.status.text())
        self.assertEqual(bob.server.text(), "")
        self.assertEqual(bob.username.text(), "")
        self.assertEqual(buddy.settings["work_chat_server"], "")
        self.assertEqual(buddy.settings["work_chat_username"], "")
        self.assertEqual(dialog.delete_password.text(), "")
        self.assertEqual(buddy.chat.pending, [])
        self.assertEqual(buddy.chat.history, [])
        self.assertIsNone(buddy.chat.semantic_pending)
        self.assertFalse(buddy.chat.semantic_loading)
        self.assertEqual(buddy.inventory_window.stock_table.rowCount(), 0)
        self.assertFalse(buddy.work_popup.isVisible())
        self.assertEqual(buddy.work_popup.notification, {})
        self.assertEqual(buddy.work_notifications._reminders, {})
        for old_token in (token, second_token):
            self.assertEqual(self.request("/api/me", token=old_token)[0], 401)
        second_pc.refresh_messages()
        wait_for(lambda: not second_pc.token, "other PC losing its deleted-account session")
        self.assertIsNone(second_pc.user)
        self.assertEqual(self.request("/api/auth/login", method="POST",
            data={"username": username, "password": PASSWORD})[0], 401)

        survivors = self.request("/api/users", token=charlie.token)[1]["users"]
        self.assertNotIn(identifier, [user["id"] for user in survivors])
        self.assertNotIn(username, [user["username"] for user in survivors])
        team_history = self.request("/api/messages", token=charlie.token)[1]["messages"]
        preserved_team = next(message for message in team_history if message["id"] == team_id)
        self.assertEqual(preserved_team["body"], "Company team history survives account deletion")
        self.assertEqual(preserved_team["sender_username"], "deleted-user")
        direct_history = self.request("/api/messages?peer_id=" + str(identifier), token=charlie.token)[1]["messages"]
        preserved_direct = next(message for message in direct_history if message["id"] == direct_id)
        self.assertEqual(preserved_direct["sender_username"], "deleted-user")
        business = self.request("/api/business", token=charlie.token)[1]["entries"]
        preserved_order = next(record for record in business if record["id"] == company_entry["id"])
        self.assertEqual(preserved_order["entry"]["title"], company_entry["title"])
        self.assertEqual(preserved_order["updated_by_username"], "deleted-user")
        stock = self.request("/api/inventory", token=charlie.token)[1]
        self.assertEqual(stock["summary"]["item_count"], 2)
        self.assertEqual(stock["summary"]["total_quantity"], 27)
        self.assertEqual(next(item for item in stock["items"] if item["sku"] == "A04-001")["location"],
                         "Aisle 2 / Shelf C / Bin 1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
