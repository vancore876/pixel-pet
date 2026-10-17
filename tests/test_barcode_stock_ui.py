"""USB keyboard scanners, reviewed stock changes, and account boundaries."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from inventory import InventoryStore
from inventory_service import InventoryService
from inventory_window import InventoryWindow
from settings import AppSettings
from test_inventory_ui import APP, ScriptedApi, wait_for, stock


class BarcodeStockUiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = AppSettings(self.root / "settings.json")
        self.settings.values["inventory_shared"] = False
        self.service = InventoryService(self.settings, self.root / "local.sqlite")
        self.window = InventoryWindow(self.service, self.settings)
        wait_for(lambda: "Local inventory" in self.service.status)
        store = InventoryStore(self.root / "local.sqlite")
        store.import_rows([stock(quantity=10, barcode="000012340001")], source="Test stock")
        store.close()
        self.service.refresh()
        wait_for(lambda: len(self.service.catalog) == 1)

    def tearDown(self):
        self.window.shutdown()
        self.service.stop()
        self.window.deleteLater()
        APP.processEvents()
        self.temp.cleanup()

    def scan(self, code="000012340001"):
        self.window.scan_input.setText(code)
        self.window.scan_input.returnPressed.emit()
        wait_for(lambda: self.window._scan_item is not None or self.window.scan_result.text() != "Looking up part…")

    def office(self):
        self.service.api.cancel_all()
        self.service.api = self.api = ScriptedApi()
        self.service.set_shared(True)
        self.service.set_session("https://office.example", "one", {"id": 1, "username": "alice"})
        item = stock(quantity=10, barcode="000012340001", revision=5)
        self.api.finish(self.api.pending("/api/inventory?"), {"items": [item], "summary": {
            "revision": 5, "item_count": 1, "total_quantity": 10, "low_stock_count": 0}})
        wait_for(lambda: self.service.online and bool(self.service.catalog))
        return item

    def office_scan(self, item):
        self.window.scan_input.setText(item["barcode"])
        self.window.scan_input.returnPressed.emit()
        self.api.finish(self.api.pending("/scan?"), {"item": item})

    def submit_sale(self, quantity=2):
        self.window.movement_kind.setCurrentIndex(self.window.movement_kind.findData("sale"))
        self.window.movement_quantity.setValue(quantity)
        self.window.movement_reason.setText("Counter sale")
        self.window.movement_reference.setText("Invoice 123")
        with patch.object(self.window, "_confirm", return_value=True):
            self.window.movement_button.click()

    def test_usb_enter_keeps_leading_zeroes_and_never_changes_stock(self):
        self.window.show_scanner()
        QTest.keyClicks(self.window.scan_input, "000012340001")
        QTest.keyClick(self.window.scan_input, Qt.Key_Return)
        wait_for(lambda: self.window._scan_item is not None)
        self.assertEqual(self.window._scan_item["barcode"], "000012340001")
        self.assertEqual(self.window.scan_input.selectedText(), "000012340001")
        self.assertIn("On hand: 10", self.window.scan_detail.text())
        self.assertIn("Aisle 3 / Bin B", self.window.scan_detail.text())
        self.assertEqual(InventoryStore(self.service._path).get("00042")["quantity"], 10)
        self.assertIsNone(self.service.pending_movement)

    def test_typing_outside_scan_field_does_not_capture_barcode(self):
        self.window.show()
        self.window.search.setFocus()
        QTest.keyClicks(self.window.search, "000012340001")
        QTest.keyClick(self.window.search, Qt.Key_Return)
        APP.processEvents()
        self.assertEqual(self.window.scan_input.text(), "")
        self.assertIsNone(self.window._scan_item)

    def test_exact_lookup_does_not_match_substring(self):
        self.scan("1234")
        self.assertIsNone(self.window._scan_item)
        self.assertEqual(self.window.scan_result.text(), "Part not found")
        self.assertFalse(self.window.movement_button.isEnabled())

    def test_unknown_barcode_requires_choose_edit_and_save(self):
        self.scan("00009999")
        self.window.stock_table.selectRow(0)
        self.assertTrue(self.window.assign_barcode_button.isEnabled())
        self.window.assign_barcode_button.click()
        self.assertEqual(self.window.editors["barcode"].text(), "00009999")
        self.assertTrue(self.window._editor_dirty)
        self.assertEqual(InventoryStore(self.service._path).get("00042")["barcode"], "000012340001")
        self.window.save_button.click()
        wait_for(lambda: not self.service.busy and self.service.catalog[0]["barcode"] == "00009999")
        self.scan("00009999")
        self.assertEqual(self.window._scan_item["sku"], "00042")

    def test_assigning_unknown_barcode_preserves_other_unsaved_part_fields(self):
        self.window.stock_table.selectRow(0)
        self.window.editors["location"].setFocus()
        self.window.editors["location"].selectAll()
        QTest.keyClicks(self.window.editors["location"], "New shelf")
        self.scan("00008888")
        self.window.assign_barcode_button.click()
        self.assertEqual(self.window.editors["location"].text(), "New shelf")
        self.assertEqual(self.window.editors["barcode"].text(), "00008888")
        self.assertEqual(InventoryStore(self.service._path).get("00042")["location"], "Aisle 3 / Bin B")

    def test_narrow_window_scrolls_instead_of_overlapping_scanner_fields(self):
        self.window.resize(760, 540)
        self.window.show_scanner()
        self.scan()
        APP.processEvents()
        widgets = (self.window.movement_kind, self.window.movement_quantity,
                   self.window.movement_reason, self.window.movement_reference)
        for earlier, later in zip(widgets, widgets[1:]):
            self.assertGreater(later.geometry().top(), earlier.geometry().bottom())
        self.assertGreater(self.window.scanner_page.verticalScrollBar().maximum(), 0)
        self.window.scanner_page.ensureWidgetVisible(self.window.movement_button)
        APP.processEvents()
        self.assertTrue(self.window.movement_button.isVisible())

    def test_cancelled_review_leaves_stock_unchanged(self):
        self.scan()
        with patch.object(self.window, "_confirm", return_value=False) as confirm:
            self.window.movement_button.click()
        self.assertIn("10 → 11", confirm.call_args.args[1])
        self.assertIsNone(self.service.pending_movement)
        self.assertEqual(InventoryStore(self.service._path).get("00042")["quantity"], 10)

    def test_sale_review_shows_reason_reference_and_before_after(self):
        self.scan()
        self.window.movement_kind.setCurrentIndex(self.window.movement_kind.findData("sale"))
        self.window.movement_quantity.setValue(2)
        self.window.movement_reason.setText("Counter sale")
        self.window.movement_reference.setText("Invoice 123")
        with patch.object(self.window, "_confirm", return_value=True) as confirm:
            self.window.movement_button.click()
        for expected in ("00042", "10 → 8", "Counter sale", "Invoice 123"):
            self.assertIn(expected, confirm.call_args.args[1])
        wait_for(lambda: self.service.summary.get("total_quantity") == 8 and not self.service.busy)
        self.assertIn("10 → 8", self.window.movement_status.text())
        self.assertFalse(self.window.movement_button.isEnabled())
        self.assertIsNone(self.service.pending_movement)

    def test_count_requires_reason_and_accepts_zero(self):
        self.scan()
        self.window.movement_kind.setCurrentIndex(self.window.movement_kind.findData("count"))
        self.window.movement_quantity.setValue(0)
        with patch.object(self.window, "_confirm", return_value=True) as confirm:
            self.window.movement_button.click()
            confirm.assert_not_called()
            self.window.movement_reason.setText("Shelf counted empty")
            self.window.movement_button.click()
            self.assertIn("10 → 0", confirm.call_args.args[1])
        wait_for(lambda: self.service.summary.get("total_quantity") == 0 and not self.service.busy)

    def test_sale_cannot_go_below_zero(self):
        self.scan()
        self.window.movement_kind.setCurrentIndex(self.window.movement_kind.findData("sale"))
        self.window.movement_quantity.setValue(11)
        with patch.object(self.window, "_confirm", return_value=True) as confirm:
            self.window.movement_button.click()
            confirm.assert_not_called()
        self.assertIn("allowed range", self.window.movement_status.text())

    def test_barcode_column_maps_and_preserves_zeroes_in_csv(self):
        path = self.root / "new.csv"
        path.write_text("SKU,Qty,Barcode\nP-2,3,000000002\n", encoding="utf-8")
        self.window.load_file(path)
        wait_for(lambda: self.window._preview is not None and not self.service.busy)
        self.assertEqual(self.window.mapping["barcode"].currentData(), 2)
        self.window.review_button.click()
        wait_for(lambda: self.service.plan is not None and not self.service.busy)
        self.assertEqual(self.service.plan.rows[0]["barcode"], "000000002")

    def test_late_scan_response_cannot_replace_newer_scan(self):
        item = self.office()
        self.service.scan_code("000012340001")
        first = self.api.pending("/scan?")
        self.service.scan_code("OTHER")
        second = [call for call in self.api.calls if "/scan?" in call["path"]][-1]
        other = {**item, "sku": "OTHER", "barcode": "OTHER"}
        self.api.finish(second, {"item": other})
        self.api.finish(first, {"item": item})
        self.assertEqual(self.window._scan_item["sku"], "OTHER")

    def test_editing_scan_input_invalidates_inflight_result(self):
        item = self.office()
        self.service.scan_code(item["barcode"])
        call = self.api.pending("/scan?")
        QTest.keyClicks(self.window.scan_input, "NEXT")
        self.api.finish(call, {"item": item})
        self.assertIsNone(self.window._scan_item)

    def test_stale_change_requires_rescan_without_retrying_write(self):
        item = self.office()
        self.office_scan(item)
        self.submit_sale()
        request = self.api.pending("/movements", "POST")
        self.api.finish(request, error="Stock changed. Scan again.", status=409)
        self.assertIsNone(self.window._scan_item)
        self.assertIsNone(self.service.pending_movement)
        self.assertFalse(self.window.movement_button.isEnabled())
        self.assertIn("another PC", self.window.movement_status.text())
        self.assertEqual(len([c for c in self.api.calls if c["method"] == "POST"]), 1)

    def test_uncertain_retry_keeps_original_payload_and_blocks_other_writes(self):
        item = self.office()
        self.office_scan(item)
        self.submit_sale()
        original = self.api.pending("/movements", "POST")
        payload = dict(original["body"])
        self.api.finish(original, error="Response lost", status=0)
        self.assertTrue(self.service.movement_uncertain)
        self.assertFalse(self.service.can_write)
        self.assertEqual(self.service.pending_movement, payload)
        self.service.update_item({**item, "quantity": 99})
        self.assertFalse(any("/imports" in c["path"] for c in self.api.calls))
        with patch.object(self.window, "_confirm", return_value=True):
            self.window.retry_movement_button.click()
        retry = self.api.pending("/movements", "POST")
        self.assertEqual(retry["body"], payload)
        self.api.finish(retry, {"item": {**item, "quantity": 8, "revision": 6}, "movement": {
            "kind": "sale", "before_quantity": 10, "after_quantity": 8}, "summary": {"revision": 6}})
        self.assertIsNone(self.service.pending_movement)
        self.assertFalse(self.service.movement_uncertain)
        self.assertIn("10 → 8", self.window.movement_status.text())

    def test_cancel_during_write_retains_uncertain_request_for_safe_retry(self):
        item = self.office()
        self.office_scan(item)
        self.submit_sale()
        payload = dict(self.service.pending_movement)
        self.service.cancel()
        self.assertEqual(self.service.pending_movement, payload)
        self.assertTrue(self.service.movement_uncertain)
        self.assertFalse(self.service.can_write)
        self.assertFalse(self.service.busy)

    def test_movement_completion_keeps_a_newer_different_scan(self):
        item = self.office()
        self.office_scan(item)
        self.submit_sale()
        write = self.api.pending("/movements", "POST")
        other = {**item, "sku": "OTHER", "barcode": "00002222"}
        self.office_scan(other)
        self.api.finish(write, {"item": {**item, "quantity": 8, "revision": 6}, "movement": {
            "kind": "sale", "before_quantity": 10, "after_quantity": 8}, "summary": {"revision": 6}})
        self.assertEqual(self.window._scan_item["sku"], "OTHER")
        self.assertTrue(self.window.movement_button.isEnabled())
        self.assertTrue(self.window.scan_result.text().startswith("OTHER"))

    def test_old_catalogue_refresh_cannot_overwrite_confirmed_stock_change(self):
        item = self.office()
        self.office_scan(item)
        self.service.refresh()
        old_refresh = self.api.pending("/api/inventory?")
        self.submit_sale()
        self.api.finish(self.api.pending("/movements", "POST"), {
            "item": {**item, "quantity": 8, "revision": 6}, "movement": {
                "kind": "sale", "before_quantity": 10, "after_quantity": 8},
            "summary": {"revision": 6, "item_count": 1, "total_quantity": 8}})
        self.api.finish(old_refresh, {"items": [item], "summary": {
            "revision": 5, "item_count": 1, "total_quantity": 10}})
        APP.processEvents()
        self.assertEqual(self.service.catalog[0]["quantity"], 8)
        self.assertEqual(self.service.summary["revision"], 6)
        fresh_refresh = self.api.pending("/api/inventory?")
        self.assertIsNot(fresh_refresh, old_refresh)

    def test_offline_scan_displays_stale_cache_and_no_write_controls(self):
        item = self.office()
        self.service.scan_code(item["barcode"])
        self.api.finish(self.api.pending("/scan?"), error="Network down", status=0)
        self.assertEqual(self.window._scan_item["sku"], item["sku"])
        self.assertIn("OFFLINE", self.window.scan_detail.text())
        self.assertIn("May be out of date", self.window.scan_detail.text())
        self.assertFalse(self.window.movement_button.isEnabled())
        self.assertFalse(self.service.can_write)
        self.assertIsNone(self.service.pending_movement)

    def test_logout_clears_scanned_item_history_and_retry_data(self):
        item = self.office()
        self.office_scan(item)
        self.window.history_filter.setText(item["sku"])
        self.window.movement_reason.setText("Private reason")
        self.submit_sale()
        self.api.finish(self.api.pending("/movements", "POST"), error="Lost", status=0)
        self.service.set_session("", "", None)
        self.assertIsNone(self.window._scan_item)
        self.assertIsNone(self.service.pending_movement)
        self.assertEqual(self.window.scan_input.text(), "")
        self.assertEqual(self.window.history_filter.text(), "")
        self.assertEqual(self.window.movement_reason.text(), "")
        self.assertEqual(self.window.movement_status.text(), "")

    def test_account_switch_during_confirmation_does_not_write_to_new_account(self):
        item = self.office()
        self.office_scan(item)
        def switch_account(*args):
            self.service.set_session("https://office.example", "two", {"id": 2, "username": "bob"})
            return True
        with patch.object(self.window, "_confirm", side_effect=switch_account):
            self.window.movement_button.click()
        self.assertFalse(any(c["method"] == "POST" for c in self.api.calls))

    def test_close_discards_pending_scan_without_closing_application(self):
        item = self.office()
        self.window.show_scanner()
        self.service.scan_code(item["barcode"])
        call = self.api.pending("/scan?")
        self.window.close()
        self.api.finish(call, {"item": item})
        self.assertFalse(self.window.isVisible())
        self.assertIsNone(self.window._scan_item)
        self.assertFalse(self.service._stopped)

    def test_history_paginates_and_displays_actor_and_quantities(self):
        self.office()
        self.window.history_filter.setText("00042")
        self.window.history_refresh_button.click()
        first = self.api.pending("/movements", "GET")
        movement = {"id": 55, "sku": "00042", "kind": "sale", "before_quantity": 10,
            "after_quantity": 8, "actor": "alice", "created_at": "2026-10-16T10:00:00Z",
            "reason": "Counter sale", "reference": "Invoice 123", "changes": {}}
        self.api.finish(first, {"movements": [movement], "has_more": True, "next_before_id": 55})
        self.assertEqual(self.window.history_table.item(0, 3).text(), "10 → 8")
        self.assertEqual(self.window.history_table.item(0, 4).text(), "alice")
        self.window.history_more_button.click()
        second = self.api.pending("/movements", "GET")
        self.assertIn("before_id=55", second["path"])
        self.api.finish(second, {"movements": [{**movement, "id": 54}], "has_more": False, "next_before_id": None})
        self.assertEqual(self.window.history_table.rowCount(), 2)
        self.assertFalse(self.window.history_more_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
