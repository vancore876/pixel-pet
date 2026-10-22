"""Inventory review, account boundaries and GUI responsiveness regressions."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import PropertyMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from inventory import InventoryStore
from inventory_service import InventoryService
from inventory_window import InventoryWindow
from settings import AppSettings

APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)


class ScriptedApi:
    def __init__(self):
        self.base_url, self.calls = "", []

    def request(self, method, path, body, callback, token=""):
        call = {"method": method, "path": path, "body": body, "callback": callback,
                "token": token, "done": False}
        self.calls.append(call)
        return call

    def cancel_all(self):
        pass

    def pending(self, suffix="", method=None):
        return next(call for call in self.calls if not call["done"]
                    and suffix in call["path"] and (method is None or call["method"] == method))

    def finish(self, call, data=None, error="", status=200):
        call["done"] = True
        call["callback"](data, error, status)


def wait_for(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        APP.processEvents()
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError("Inventory did not reach the expected state")


def stock(sku="00042", quantity=1, **extra):
    return {"sku": sku, "quantity": quantity, "description": "Brake pads", "location": "Aisle 3 / Bin B",
            "reorder_point": 2, "reorder_quantity": 12, "target_stock": 20, "supplier": "Parts Co", **extra}


class InventoryUiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = AppSettings(self.root / "settings.json")
        self.settings.values["inventory_shared"] = False
        self.service = InventoryService(self.settings, self.root / "local.sqlite")
        self.window = InventoryWindow(self.service, self.settings)
        wait_for(lambda: "Local inventory" in self.service.status)

    def tearDown(self):
        self.window.shutdown()
        self.service.stop()
        self.service._executor.shutdown(wait=True, cancel_futures=True)
        self.window.deleteLater()
        APP.processEvents()
        self.temp.cleanup()

    def csv(self, content, name="stock.csv"):
        path = self.root / name
        path.write_text(content, encoding="utf-8")
        return path

    def load(self, content):
        path = self.csv(content)
        self.window.tabs.setCurrentIndex(1)
        self.window.load_file(path)
        wait_for(lambda: self.window._preview is not None and not self.service.busy)
        return path

    def review(self):
        self.window.review_button.click()
        wait_for(lambda: self.service.plan is not None and not self.service.busy)

    def save_plan(self):
        self.window.import_button.click()
        wait_for(lambda: not self.service.busy and self.service.summary.get("item_count", 0) > 0)

    def seed(self, rows):
        store = InventoryStore(self.root / "local.sqlite")
        store.import_rows(rows, source="Seed stock")
        self.service.refresh()
        wait_for(lambda: len(self.service.catalog) == len(rows))

    def test_confirm_click_before_background_check_imports_once(self):
        self.load('SKU,Qty,Location\nGOOD,3,Rack A\nBAD,-1,Skipped bin\n')
        self.window._review_timer.stop()
        self.assertTrue(self.window.skip_invalid_quantities.isChecked())
        self.assertTrue(self.window.import_button.isEnabled())
        with patch.object(self.service, 'commit_import', wraps=self.service.commit_import) as commit:
            self.window.import_button.click()
            wait_for(lambda: not self.service.busy and self.service.summary['item_count'] == 1)
            self.assertEqual(commit.call_count, 1)
        self.assertEqual(self.service.catalog[0]['sku'], 'GOOD')

    def test_confirm_retries_a_background_check_interrupted_by_busy_operation(self):
        self.load('SKU,Qty\nGOOD,3\n')
        self.window._review_timer.stop()
        self.service._busy(True)
        self.window._review()
        self.assertIsNone(self.service.plan)
        self.service._busy(False)
        self.assertTrue(self.window.import_button.isEnabled())
        self.window.import_button.click()
        wait_for(lambda: not self.service.busy and self.service.summary['item_count'] == 1)

    def test_file_can_be_selected_without_office_access_and_confirm_explains_signin(self):
        self.service.shared = True
        self.service.online = False
        self.service.token, self.service.user = '', None
        self.load('SKU,Qty\nGOOD,3\n')
        self.assertTrue(self.window.choose_button.isEnabled())
        self.assertTrue(self.window.import_button.isEnabled())
        self.window.import_button.click()
        self.assertIn('Sign in to Work Chat', self.window.import_hint.text())
        self.assertIsNone(self.service.plan)
        self.assertEqual(self.service.summary['item_count'], 0)

    def test_confirm_explains_missing_columns_and_does_not_write(self):
        self.load('Our code,Our quantity\nGOOD,3\n')
        self.assertTrue(self.window.import_button.isEnabled())
        self.window.import_button.click()
        self.assertIn('Part Number and Quantity', self.window.import_hint.text())
        self.assertEqual(self.service.summary['item_count'], 0)

    def test_confirm_explains_offline_access_without_bypassing_authorization(self):
        self.load('SKU,Qty\nGOOD,3\n')
        self.window._review_timer.stop()
        with patch.object(type(self.service), 'can_write', new_callable=PropertyMock, return_value=False):
            with patch.object(self.service, 'commit_import') as commit:
                self.window.import_button.click()
                commit.assert_not_called()
                self.assertIn('Sign in', self.window.import_hint.text())
        self.assertEqual(self.service.summary['item_count'], 0)

    def test_changing_columns_cancels_a_queued_confirmation(self):
        self.load('SKU,Qty,Location\nGOOD,3,Rack A\n')
        self.review()
        self.window._confirm_after_review = True
        self.window._show_plan({'plan': self.service.plan, 'preview': self.service.import_preview})
        self.window.mapping['location'].setCurrentIndex(0)
        self.window._review_timer.stop()
        APP.processEvents()
        self.assertFalse(self.window._confirm_after_review)
        self.assertEqual(self.service.summary['item_count'], 0)

    def test_cancel_button_clears_pending_confirmation(self):
        self.load('SKU,Qty\nGOOD,3\n')
        self.window._confirm_after_review = True
        self.window._cancel_operation()
        self.assertFalse(self.window._confirm_after_review)
        self.assertFalse(self.window._review_timer.isActive())
        self.assertEqual(self.service.summary['item_count'], 0)

    def test_quantity_errors_can_be_bypassed_explicitly_and_valid_rows_saved(self):
        self.seed([stock()])
        self.window.skip_invalid_quantities.setChecked(False)
        self.load('SKU,Qty,Location\n00042,-5,Do not overwrite\nGOOD,3,Rack A\n')
        wait_for(lambda: self.service.plan is not None and not self.service.busy)
        self.assertTrue(self.window.import_button.isEnabled())
        self.window.skip_invalid_quantities.setChecked(True)
        wait_for(lambda: self.service.plan is not None and not self.service.busy and self.window.import_button.isEnabled())
        self.assertIn('Skipped 1 rows with invalid quantities', self.window.review_text.toPlainText())
        self.assertEqual([row['sku'] for row in self.service.plan.rows], ['GOOD'])
        self.window.import_button.click()
        wait_for(lambda: not self.service.busy and self.service.summary['item_count'] == 2)
        old = next(row for row in self.service.catalog if row['sku'] == '00042')
        self.assertEqual(old['quantity'], 1)
        self.assertEqual(old['location'], 'Aisle 3 / Bin B')

    def test_zero_quantity_option_and_added_columns_import_without_extra_steps(self):
        self.load('Part Number,Quantity,Location,AlternatePartNumber,SubstitutePartNumber,Retail Price,Cost Price\n00042,0,Rack A,ALT-9,SUB-9,1250.50,800.25\nP-2,3,Rack B,ALT-2,SUB-2,20,10\n')
        wait_for(lambda: self.service.plan is not None and not self.service.busy and self.window.import_button.isEnabled())
        self.assertTrue(self.window.include_zero.isChecked())
        self.assertEqual(len(self.service.plan.rows), 2)
        self.assertEqual(self.window.import_table.columnCount(), 7)
        self.window.include_zero.setChecked(False)
        wait_for(lambda: self.service.plan is not None and not self.service.busy and self.window.import_button.isEnabled())
        self.assertEqual([row['sku'] for row in self.service.plan.rows], ['P-2'])
        self.window.include_zero.setChecked(True)
        wait_for(lambda: self.service.plan is not None and not self.service.busy and self.window.import_button.isEnabled())
        self.window.import_button.click()
        wait_for(lambda: not self.service.busy and self.service.summary['item_count'] == 2)
        self.window.stock_table.selectRow(0)
        self.assertEqual(self.window.editors['quantity'].value(), 0)
        self.assertEqual(self.window.editors['retail_price'].text(), '1250.50')
        self.window.editors['cost_price'].setText('825.75')
        self.window.save_button.click()
        wait_for(lambda: not self.service.busy and self.service.catalog[0]['cost_price'] == '825.75')
        self.window.search.setText('SUB-9')
        self.window.search.returnPressed.emit()
        self.assertEqual(self.window.stock_table.item(0, 0).text(), '00042')

    def test_excel_import_needs_only_three_columns_and_one_confirmation(self):
        from openpyxl import Workbook
        path = self.root / 'parts.xlsx'
        book = Workbook()
        book.active.append(['Part Number', 'Quantity', 'Location', 'Barcode', 'Minimum'])
        book.active.append(['00042', 7, 'Rack 9', 'ignored optional code', 'not a number'])
        book.save(path)
        book.close()
        self.seed([stock()])
        self.window.tabs.setCurrentIndex(1)
        self.window.load_file(path)
        wait_for(lambda: self.service.plan is not None and not self.service.busy and self.window.import_button.isEnabled())
        self.assertTrue(self.window.optional_mapping.isHidden())
        self.assertTrue(self.window.review_button.isHidden())
        self.assertEqual(self.window.import_table.columnCount(), 3)
        self.assertEqual(set(self.service.plan.mapping), {'sku', 'quantity', 'location'})
        self.assertEqual(self.service.catalog[0]['quantity'], 1)
        self.window.import_button.click()
        wait_for(lambda: not self.service.busy and self.service.catalog[0]['quantity'] == 7)
        row = self.service.catalog[0]
        self.assertEqual(row['sku'], '00042')
        self.assertEqual(row['location'], 'Rack 9')
        self.assertEqual(row['description'], 'Brake pads')
        self.assertEqual(row['supplier'], 'Parts Co')
        self.assertEqual(row['reorder_point'], 2)

    def test_custom_three_column_mapping_checks_automatically(self):
        self.load('Our code,Available units,Rack detail\nP-7,5,Rack 8\n')
        for field, column in [('sku', 0), ('quantity', 1), ('location', 2)]:
            combo = self.window.mapping[field]
            combo.setCurrentIndex(combo.findData(column))
        wait_for(lambda: self.service.plan is not None and not self.service.busy and self.window.import_button.isEnabled())
        self.assertEqual(self.service.plan.rows[0], {'sku': 'P-7', 'quantity': 5, 'location': 'Rack 8'})
        self.assertEqual(self.service.summary['item_count'], 0)

    def test_automatic_check_blocks_invalid_rows(self):
        self.window.skip_invalid_quantities.setChecked(False)
        self.load('SKU,Qty,Location\nP-7,-1,Rack 8\n')
        wait_for(lambda: self.service.plan is not None and not self.service.busy)
        self.assertTrue(self.window.import_button.isEnabled())
        self.assertIn('nonnegative', self.window.review_text.toPlainText())
        self.window.import_button.click()
        self.assertIn('Import blocked:', self.window.import_hint.text())
        self.assertEqual(self.service.summary['item_count'], 0)

    def test_file_review_is_explicit_and_stock_search_keeps_leading_zeroes(self):
        self.window.optional_import.setChecked(True)
        self.load("Part Number,Description,Qty,Bin,Minimum,Restock Qty\n00042,Brake pads,1,Shelf A,2,12\n00051,Oil filter,8,Shelf C,2,6\n")
        self.assertEqual(self.service.summary["item_count"], 0)
        self.assertEqual(self.window.mapping["sku"].currentData(), 0)
        self.review()
        self.assertTrue(self.window.import_button.isEnabled())
        self.assertEqual(InventoryStore(self.root / "local.sqlite").summary()["item_count"], 0)
        self.save_plan()
        self.window.search.setText("00042")
        self.window.search.returnPressed.emit()
        self.assertEqual(self.window.stock_table.item(0, 0).text(), "00042")
        self.assertEqual(self.window.stock_table.item(0, 3).text(), "Shelf A")
        self.window.search.clear()
        self.window.low_stock.setChecked(True)
        self.assertEqual(self.window.stock_table.rowCount(), 1)
        self.assertEqual(self.service.context("Where is 00042?")[0]["location"], "Shelf A")
        self.assertEqual(self.service.context("Which items should we restock?")[0]["sku"], "00042")

    def test_invalid_rows_show_errors_without_replacing_existing_stock(self):
        self.window.skip_invalid_quantities.setChecked(False)
        self.seed([stock()])
        errors = []
        self.service.error.connect(errors.append)
        self.load("SKU,Qty\n00042,-2\n")
        self.review()
        self.assertFalse(self.service.plan.valid)
        self.assertTrue(self.window.import_button.isEnabled())
        self.assertIn("nonnegative", self.window.review_text.toPlainText())
        self.assertEqual(InventoryStore(self.root / "local.sqlite").get("00042")["quantity"], 1)
        self.assertEqual(errors, [])

    def test_custom_headers_require_editable_mapping(self):
        self.load("Our code,Available units,Rack detail\nP-7,5,Rack 8\n")
        self.assertFalse(self.window.review_button.isEnabled())
        self.window.mapping["sku"].setCurrentIndex(self.window.mapping["sku"].findData(0))
        self.window.mapping["quantity"].setCurrentIndex(self.window.mapping["quantity"].findData(1))
        self.window.mapping["location"].setCurrentIndex(self.window.mapping["location"].findData(2))
        self.assertTrue(self.window.review_button.isEnabled())
        self.review()
        self.assertEqual(self.service.plan.rows[0]["location"], "Rack 8")

    def test_review_table_displays_reparsed_values_and_commit_uses_reviewed_rows(self):
        path = self.load("SKU,Qty,Location\nP-7,5,Old shelf\n")
        path.write_text("SKU,Qty,Location\nP-7,9,Reviewed shelf\n", encoding="utf-8")
        self.review()
        self.assertEqual(self.window.import_table.item(0, 1).text(), "9")
        self.assertEqual(self.window.import_table.item(0, 2).text(), "Reviewed shelf")
        path.write_text("SKU,Qty,Location\nP-7,99,Changed after review\n", encoding="utf-8")
        self.save_plan()
        self.assertEqual(self.service.catalog[0]["quantity"], 9)

    def test_background_cache_reads_every_page_beyond_500_and_context_is_indexed(self):
        self.seed([stock(f"P-{number:04}") for number in range(751)])
        self.assertEqual(len(self.service.catalog), 751)
        self.assertEqual(self.service.context("Where is P-0750?")[0]["sku"], "P-0750")
        self.assertIn("updated_at", self.service.context("P-0750")[0])
        self.assertIn("suggested_restock", self.service.context("P-0750")[0])

    def test_refresh_preserves_dirty_manual_edit_and_rejects_stale_save(self):
        self.seed([stock()])
        self.window.stock_table.selectRow(0)
        self.window.editors["location"].setFocus()
        self.window.editors["location"].selectAll()
        QTest.keyClicks(self.window.editors["location"], "My unsaved shelf")
        original_revision = self.window._edit_revision
        self.assertTrue(self.window._editor_dirty)
        InventoryStore(self.root / "local.sqlite").import_rows([{"sku": "00042", "quantity": 4}])
        self.service.refresh()
        wait_for(lambda: self.service.summary["revision"] > original_revision)
        self.assertEqual(self.window.editors["location"].text(), "My unsaved shelf")
        self.assertEqual(self.window._edit_revision, original_revision)
        errors = []
        self.service.error.connect(errors.append)
        self.window.save_button.click()
        wait_for(lambda: bool(errors))
        self.assertIn("Stock changed", errors[0])
        self.assertEqual(InventoryStore(self.root / "local.sqlite").get("00042")["quantity"], 4)

    def test_parse_runs_off_gui_thread_and_cancellation_discards_result(self):
        self.load("SKU,Qty\nA1,3\n")
        entered, release = threading.Event(), threading.Event()
        from inventory import parse_excel
        main_thread = threading.get_ident()
        seen = []
        def slow_parse(*args, **kwargs):
            seen.append(threading.get_ident())
            entered.set()
            release.wait(2)
            return parse_excel(*args, **kwargs)
        with patch("inventory.parse_excel", side_effect=slow_parse):
            self.window.review_button.click()
            wait_for(entered.is_set)
            self.window.search.setText("Still responsive")
            self.service.cancel()
            release.set()
            self.service._executor.submit(lambda: None).result(timeout=3)
            APP.processEvents()
        self.assertNotEqual(seen, [main_thread])
        self.assertIsNone(self.service.plan)
        self.assertFalse(self.service.busy)

    def test_stop_is_bounded_when_network_file_read_is_blocked(self):
        self.load("SKU,Qty\nA1,3\n")
        entered, release = threading.Event(), threading.Event()
        from inventory import parse_excel
        def blocked_parse(*args, **kwargs):
            entered.set()
            release.wait(5)
            return parse_excel(*args, **kwargs)
        try:
            with patch("inventory.parse_excel", side_effect=blocked_parse):
                self.window.review_button.click()
                wait_for(entered.is_set)
                queued = self.service._executor.submit(lambda: "Must be cancelled")
                self.assertTrue(self.service._executor._thread.daemon)
                started = time.monotonic()
                self.service.stop()
                self.assertLess(time.monotonic() - started, 1)
                self.assertTrue(queued.cancelled())
                self.assertIsNone(self.service.plan)
        finally:
            release.set()
            self.service._executor._thread.join(timeout=2)
        self.assertFalse(self.service._executor._thread.is_alive())
        APP.processEvents()
        self.assertIsNone(self.service.plan)

    def office(self):
        self.service.api.cancel_all()
        self.service.api = self.api = ScriptedApi()
        self.service.set_shared(True)
        self.service.set_session("https://office.example", "token-one", {"id": 1, "username": "alice"})
        call = self.api.pending("/api/inventory?")
        return call

    def finish_stock(self, rows):
        call = self.api.pending("/api/inventory?")
        self.api.finish(call, {"items": rows, "summary": {"revision": 5, "item_count": len(rows),
            "total_quantity": sum(row["quantity"] for row in rows), "low_stock_count": len(rows),
            "last_import_at": "2026-10-14T12:00:00Z", "last_import_source": "Office.xlsx"}})
        wait_for(lambda: self.service.online and len(self.service.catalog) == len(rows))

    def test_office_cache_is_account_scoped_and_logout_clears_preview_and_context(self):
        self.office()
        self.finish_stock([stock()])
        self.assertTrue(self.service.can_write)
        path_one = self.service._path
        self.load("SKU,Qty\nOther,2\n")
        self.service.set_session("https://office.example", "", None)
        self.assertEqual(self.service.catalog, [])
        self.assertEqual(self.service.context("00042"), [])
        self.assertIsNone(self.window._preview)
        self.assertEqual(self.window.import_table.rowCount(), 0)
        self.assertEqual(self.window.file_label.text(), "No stock file selected")
        self.service.set_session("https://office.example", "token-two", {"id": 2, "username": "bob"})
        self.assertNotEqual(self.service._path, path_one)
        self.api.finish(self.api.pending("/api/inventory?"), error="Offline", status=0)
        wait_for(lambda: "Offline" in self.service.status)
        self.assertEqual(self.service.catalog, [])
        self.assertFalse(self.service.can_write)

    def test_auth_revocation_clears_stock_instead_of_loading_cache(self):
        self.office()
        self.finish_stock([stock()])
        expired = []
        self.service.auth_expired.connect(lambda: expired.append(True))
        self.service.refresh()
        self.api.finish(self.api.pending("/api/inventory?"), error="Session expired", status=401)
        self.assertEqual(expired, [True])
        self.assertEqual(self.service.catalog, [])
        self.assertEqual(self.service.token, "")
        self.assertFalse(self.service.can_write)

    def test_remote_import_chunks_have_offsets_and_fit_request_byte_limit(self):
        self.office()
        self.finish_stock([])
        rows = [stock(f"U-{n}", description="😀" * 2000, location="😀" * 500,
                      supplier="😀" * 500) for n in range(70)]
        self.service._commit_rows(rows, "Import.xlsx", 5)
        self.api.finish(self.api.pending("/api/inventory/imports", "POST"), {"id": "a1b2"})
        received = 0
        while received < len(rows):
            call = self.api.pending("/rows", "POST")
            self.assertEqual(call["body"]["offset"], received)
            self.assertLess(len(json.dumps(call["body"], ensure_ascii=False).encode("utf-8")), 200 * 1024)
            received += len(call["body"]["rows"])
            self.api.finish(call, {"rows": received})
        commit = self.api.pending("/commit", "POST")
        self.assertEqual(commit["body"], {})
        self.assertTrue(self.service.busy)

    def test_fetch_page_size_fits_transport_response_bound(self):
        call = self.office()
        self.assertIn("limit=50", call["path"])
        rows = [stock(f"U-{n}", description="😀" * 2000, location="😀" * 500,
                      supplier="😀" * 500) for n in range(50)]
        response = {"items": rows, "summary": {"revision": 1}}
        self.assertLess(len(json.dumps(response, ensure_ascii=False).encode("utf-8")), 1024 * 1024)
        self.api.finish(call, response)
        next_page = self.api.pending("/api/inventory?")
        self.assertIn("offset=50", next_page["path"])


if __name__ == "__main__":
    unittest.main()
