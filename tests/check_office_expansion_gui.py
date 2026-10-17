"""Real HTTP + BuddyApp check: USB barcode input, receipts, overdue orders, backups.

Every account, workbook, order and backup is disposable. BUDDY_QA_DIR optionally
retains screenshots. QTest supplies the text + Enter of a USB keyboard scanner;
this does not claim a physical scanner or native Windows deployment was tested.
"""
from __future__ import annotations

from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["GROQ_API_KEY"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from openpyxl import Workbook
import check_office_update_gui as office

APP, wait_for = office.APP, office.wait_for
IT_PASSWORD = "disposable expanded office IT passphrase"
BARCODE = "000123456789"


class OfficeExpansionIntegrationTests(office.OfficeUpdateIntegrationTests):
    """Reuse the disposable server/BuddyApp fixture, replacing its scenario."""

    def test_real_office_updates(self):
        alice, _ = self.account("expansion_alice")
        bob = self.buddy.work_chat
        self.authenticate(bob, "expansion_bob")
        wait_for(lambda: self.buddy.inventory.can_write and not self.buddy.inventory._syncing,
                 "initial shared inventory")
        self.check_scanner_stock(alice, bob)
        self.check_read_receipts(alice, bob)
        self.check_overdue_orders(alice, bob)
        self.check_verified_backup(bob)
        self.buddy.shutdown()
        self.assertFalse(self.buddy.thread.isRunning())
        self.assertFalse(self.buddy.order_escalations.timer.isActive())
        self.assertFalse(self.buddy.work_chat.read_timer.isActive())

    def scan(self, code):
        window = self.buddy.inventory_window
        self.buddy.show_barcode_scanner()
        window.scan_input.setFocus()
        window.scan_input.selectAll()
        QTest.keyClicks(window.scan_input, code)
        QTest.keyClick(window.scan_input, Qt.Key_Return)
        wait_for(lambda: window.scan_result.text() != "Looking up part…", "USB scanner lookup " + code)
        self.assertTrue(window.isVisible())
        self.assertIs(window.tabs.currentWidget(), window.scanner_page)

    def check_scanner_stock(self, alice, bob):
        service, window = self.buddy.inventory, self.buddy.inventory_window
        workbook_path = self.root / "barcoded-office-stock.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Parts"
        sheet.append(["Part number", "Description", "Quantity", "Location", "Barcode", "Reorder level"])
        sheet.append(["BRAKE-01", "Front brake pads", 3, "Aisle 2 / Shelf B / Bin 7", BARCODE, 5])
        sheet.append(["FILTER-01", "Oil filter", 12, "Aisle 4 / Bin 2", "009876543210", 4])
        workbook.save(workbook_path)
        workbook.close()
        self.buddy.show_inventory()
        window.tabs.setCurrentIndex(1)
        with patch("inventory_window.QFileDialog.getOpenFileName", return_value=(str(workbook_path), "Excel (*.xlsx)")):
            window.choose_button.click()
        wait_for(lambda: service.preview is not None and not service.busy, "barcode workbook headers")
        window.review_button.click()
        wait_for(lambda: service.plan is not None and not service.busy, "barcode import review")
        self.assertTrue(service.plan.valid, service.plan.errors)
        self.assertEqual(service.plan.rows[0]["barcode"], BARCODE, "Leading zeroes must survive Excel review")
        window.import_button.click()
        wait_for(lambda: not service.busy and not service._syncing and service.summary.get("item_count") == 2,
                 "shared barcode import")
        self.scan(BARCODE)
        self.assertEqual(window._scan_item["sku"], "BRAKE-01")
        self.assertIn("Aisle 2 / Shelf B / Bin 7", window.scan_detail.text())
        self.assertIn(BARCODE, window.scan_detail.text())
        stock = self.request("/api/inventory/items/BRAKE-01", token=alice.token)[1]
        self.assertEqual(stock["quantity"], 3, "A scanner lookup must not change stock")
        self.assertEqual(stock["barcode"], BARCODE)
        window.movement_quantity.setValue(4)
        window.movement_reason.setText("Received supplier delivery")
        window.movement_reference.setText("DELIVERY-QA-001")
        with patch.object(window, "_confirm", return_value=False):
            window.movement_button.click()
        self.assertEqual(self.request("/api/inventory/items/BRAKE-01", token=alice.token)[1]["quantity"], 3)
        with patch.object(window, "_confirm", return_value=True) as review:
            window.movement_button.click()
        self.assertIn("Stock: 3 → 7", review.call_args.args[1])
        wait_for(lambda: not service.busy and not service._syncing and not service.pending_movement,
                 "reviewed receive transaction")
        self.assertIn("Saved receive", window.movement_status.text())
        self.assertEqual(self.request("/api/inventory/items/BRAKE-01", token=alice.token)[1]["quantity"], 7)
        self.assertFalse(window.movement_button.isEnabled(), "Scan again before another stock action")
        self.screenshot(window, "office-barcode-received-stock.png")
        self.scan(BARCODE)
        self.assertEqual(window._scan_item["quantity"], 7)
        window.scan_history_button.click()
        wait_for(lambda: window.history_table.rowCount() >= 2, "import and receive history")
        rows = [[window.history_table.item(row, column).text() for column in range(8)]
                for row in range(window.history_table.rowCount())]
        receive = [row for row in rows if row[2] == "receive"]
        self.assertEqual(len(receive), 1)
        self.assertEqual(receive[0][3], "3 → 7")
        self.assertEqual(receive[0][4], bob.user["username"])
        self.assertEqual(receive[0][6], "DELIVERY-QA-001")
        self.screenshot(window, "office-stock-movement-history.png")
        self.scan("FILTER-01")
        self.assertEqual(window._scan_item["sku"], "FILTER-01", "Exact part numbers also scan")
        self.scan("NO-SUCH-PART")
        self.assertIsNone(window._scan_item)
        self.assertFalse(window.movement_button.isEnabled())
        window.hide()

    def foreground(self, window):
        window.showNormal()
        window.raise_()
        window.activateWindow()
        window.composer.setFocus()
        APP.processEvents()
        # Headless Qt can deliver a prior hide's deactivation after requestActivate.
        # Activate the actual Qt window; all production visibility/focus guards remain intact.
        if APP.platformName() in ("offscreen", "minimal"):
            APP.setActiveWindow(window)
            APP.processEvents()
        self.assertIs(APP.activeWindow(), window)
        self.assertTrue(window.isActiveWindow())

    def receipt(self, sender, message_id, peer=None):
        query = "?limit=50" + ("&peer_id=" + str(peer) if peer is not None else "")
        status, payload, _ = self.request("/api/read-receipts" + query, token=sender.token)
        self.assertEqual(status, 200, payload)
        return next(row for row in payload["receipts"] if row["message_id"] == message_id)

    def check_read_receipts(self, alice, bob):
        service, popup = self.buddy.work_notifications, self.buddy.work_popup
        service.timer.setInterval(50)
        wait_for(lambda: service._cursor is not None and not service._request_pending, "incoming notification baseline")
        bob.hide()
        direct = self.send(alice, "Receipt QA: please check the delivery", bob.user["id"])
        self.receive(direct, "Receipt QA: please check the delivery")
        self.assertEqual(self.receipt(alice, direct, bob.user["id"])["read_count"], 0,
                         "A hidden chat's notification popup is not a read receipt")
        popup.dismiss_button.click()
        service.timer.stop()
        self.choose(bob, None)
        self.foreground(bob)
        bob._maybe_mark_read()
        self.assertEqual(self.receipt(alice, direct, bob.user["id"])["read_count"], 0,
                         "Reading Team Room must not mark a personal message read")
        self.choose(bob, alice.user["id"])
        self.foreground(bob)
        self.assertTrue(bob._conversation_is_readable())
        bob._maybe_mark_read()
        wait_for(lambda: bob._read_through >= direct, "real personal read receipt POST")
        self.assertEqual(self.receipt(alice, direct, bob.user["id"])["read_count"], 1)
        self.assertIn(direct, service._reminders, "Reading and acknowledging a reminder are separate")
        alice._refresh_receipts()
        wait_for(lambda: "  ·  Read\n" in alice.transcript.toPlainText(), "sender's personal Read label")
        self.screenshot(alice, "office-personal-read-receipt.png")

        # Scroll away from the newest incoming message before any read timer runs.
        bob.hide()
        status, payload, _ = self.request("/api/messages", method="POST", token=alice.token,
            data={"recipient_id": bob.user["id"], "body": "Long delivery checklist\n" * 90})
        self.assertEqual(status, 201, payload)
        long_message = payload["message"]["id"]
        bob.show()
        wait_for(lambda: not bob._messages_pending and long_message in bob._messages, "long incoming delivery checklist")
        scroll = bob.transcript.verticalScrollBar()
        self.assertGreater(scroll.maximum(), 0)
        scroll.setValue(0)
        self.foreground(bob)
        bob._maybe_mark_read()
        self.assertEqual(self.receipt(alice, long_message, bob.user["id"])["read_count"], 0,
                         "Unread scrolled history must not be marked read")
        scroll.setValue(scroll.maximum())
        bob._maybe_mark_read()
        wait_for(lambda: bob._read_through >= long_message, "read after scrolling to newest content")

        self.foreground(alice)
        team = self.send(alice, "Receipt QA: team restock is ready")
        self.choose(bob, None)
        self.foreground(bob)
        bob._maybe_mark_read()
        wait_for(lambda: bob._read_through >= team, "real Team Room receipt POST")
        self.assertEqual(self.receipt(alice, team)["read_count"], 1)
        alice._refresh_receipts()
        wait_for(lambda: "Read by 1" in alice.transcript.toPlainText(), "Team Room Read by count")
        self.screenshot(alice, "office-team-read-receipt.png")
        service.acknowledge(direct)
        service.timer.stop()
        popup.dismiss()
        alice.hide()
        bob.hide()

    def check_overdue_orders(self, alice, bob):
        service, popup = self.buddy.order_escalations, self.buddy.overdue_popup
        test_now = [time.time()]
        service.clock = lambda: test_now[0]
        identifier = "e" * 32
        entry = {"id": identifier, "kind": "order", "title": "Customer brake pad pickup",
                 "customer": "QA Customer", "order_ref": "QA-PICKUP-001", "order_type": "order",
                 "order_status": "ready", "order_due": test_now[0] - 90}
        status, saved, _ = self.request("/api/business/" + identifier, method="PUT", token=alice.token,
                                       data={"expected_revision": 0, "entry": entry})
        self.assertEqual(status, 200, saved)
        self.buddy.business_sync.refresh()
        wait_for(lambda: self.buddy.business_sync.ready and identifier in self.buddy.business_sync._records,
                 "shared customer order snapshot")
        service.pump()
        wait_for(lambda: popup.isVisible() and popup.identifier == identifier, "initial customer order popup")
        self.assertEqual(popup.notification["tier"], 0)
        self.assertLessEqual(popup.width(), 350)
        self.assertLessEqual(popup.height(), 178)
        self.assertTrue(popup.windowFlags() & Qt.WindowDoesNotAcceptFocus)
        self.screenshot(popup, "office-overdue-compact-popup.png")
        popup.snooze_button.menu().actions()[0].trigger()  # Later → In 15 minutes.
        self.assertFalse(popup.isVisible())
        self.buddy.show_overdue_orders()
        board = self.buddy.overdue_window
        self.assertEqual(board.table.rowCount(), 1)
        self.assertIn("Snoozed until", board.table.item(0, 5).text())
        self.assertEqual(service.rows[0]["snooze_until"], test_now[0] + 900)
        self.screenshot(board, "office-overdue-board-snoozed.png")
        unchanged = self.request("/api/business", token=bob.token)[1]["entries"]
        current = next(row for row in unchanged if row["id"] == identifier)
        self.assertEqual(current["entry"]["order_due"], entry["order_due"])
        self.assertEqual(current["entry"]["order_status"], "ready")
        service.pump()
        self.assertFalse(popup.isVisible())
        test_now[0] += 901
        service.pump()
        wait_for(lambda: popup.isVisible(), "expired personal snooze")
        popup.clear()
        test_now[0] += 3600
        service.pump()
        wait_for(lambda: popup.isVisible() and popup.notification["tier"] == 1, "one-hour escalation")
        self.screenshot(popup, "office-overdue-escalated-popup.png")
        completed = dict(current["entry"], done=True, order_status="delivered")
        status, result, _ = self.request("/api/business/" + identifier, method="PUT", token=alice.token,
            data={"expected_revision": current["revision"], "entry": completed})
        self.assertEqual(status, 200, result)
        self.buddy.business_sync.refresh()
        wait_for(lambda: self.buddy.business_sync._records[identifier]["entry"]["done"], "shared completed order")
        service.pump()
        self.assertEqual(service.rows, [])
        self.assertFalse(popup.isVisible())
        self.assertEqual(board.table.rowCount(), 0)
        board.hide()

    def check_verified_backup(self, bob):
        self.buddy.show_home()
        self.buddy.home.it_button.click()
        window = self.buddy.it_window
        window.server_input.setText(self.address)
        window.password_input.setText("admin123")
        QTest.keyClick(window.password_input, Qt.Key_Return)
        wait_for(lambda: bool(window.token), "IT bootstrap login")
        self.assertIs(window.pages.currentWidget(), window.password_page)
        window.current_password.setText("admin123")
        window.new_password.setText(IT_PASSWORD)
        window.confirm_password.setText(IT_PASSWORD)
        QTest.keyClick(window.confirm_password, Qt.Key_Return)
        wait_for(lambda: window.pages.currentWidget() is window.dashboard_page
                 and not window._refreshing and not window._backups_refreshing,
                 "IT setup and backup dashboard")
        self.assertTrue(window.backup_now_button.isEnabled(), window.backup_status.text())
        self.assertEqual(self.request("/api/it/backups", token=bob.token)[0], 401)
        window.backup_now_button.click()
        dialog = window._backup_dialog
        self.assertTrue(dialog.isVisible())
        dialog.admin_password.setText(IT_PASSWORD)
        dialog.submit_button.click()
        wait_for(lambda: not dialog.isVisible(), "manual IT backup accepted")
        self.assertEqual(dialog.admin_password.text(), "")
        wait_for(lambda: window.backups_table.rowCount() > 0 and not window._backups_running,
                 "verified manual backup shown in IT", seconds=20)
        self.assertEqual(window.backups_table.item(0, 3).text(), "Yes")
        status, result, _ = self.request("/api/it/backups", token=window.token)
        self.assertEqual(status, 200, result)
        self.assertFalse(result["running"])
        self.assertTrue(result["backups"][0]["verified"])
        path = Path(result["directory"]) / result["backups"][0]["name"]
        self.assertTrue(path.is_relative_to(self.root), "The backup must stay inside the disposable fixture")
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as snapshot:
            self.assertEqual(snapshot.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertGreater(snapshot.execute("SELECT COUNT(*) FROM read_receipts").fetchone()[0], 0)
            self.assertEqual(snapshot.execute("SELECT COUNT(*) FROM inventory_movements WHERE kind='receive'").fetchone()[0], 1)
        self.screenshot(window, "office-it-verified-backup.png")
        self.assertTrue(bob.token, "An IT backup must not sign the employee out")


if __name__ == "__main__":
    unittest.main(verbosity=2)
