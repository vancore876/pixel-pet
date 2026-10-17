"""Scanner fidelity, atomic stock changes, migrations, and durable audit records."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from inventory import COUNT_LIMIT, InventoryError, InventoryStore, parse_excel


class BarcodeStockTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.store = InventoryStore(self.root / "stock.sqlite")
        self.store.import_rows([{"sku": "FILTER-1", "quantity": 10, "barcode": "000123456789",
                                 "location": "A-2", "reorder_point": 3}])

    def tearDown(self):
        self.directory.cleanup()

    def move(self, kind="sale", quantity=1, **extra):
        payload = {"expected_revision": self.store.get("FILTER-1")["revision"], "request_id": uuid.uuid4().hex}
        return self.store.record_movement("FILTER-1", kind, quantity, **(payload | extra))

    def test_scan_is_exact_preserves_zeroes_and_never_changes_stock(self):
        before = self.store.summary()
        for code in ("000123456789", "filter-1", " FILTER-1 "):
            self.assertEqual(self.store.resolve_code(code)["location"], "A-2")
        for code in ("123456789", "123", "FILTER%", "FILTER_", "A-2"):
            with self.subTest(code=code), self.assertRaises(InventoryError) as caught:
                self.store.resolve_code(code)
            self.assertEqual(caught.exception.status, 404)
        self.assertEqual(self.store.summary(), before)
        self.assertEqual(len(self.store.movements()["movements"]), 1)

    def test_ambiguous_barcode_and_sku_never_picks_arbitrary_item(self):
        self.store.import_rows([{"sku": "000123456789", "quantity": 5}])
        with self.assertRaises(InventoryError) as caught:
            self.store.resolve_code("000123456789")
        self.assertEqual(caught.exception.status, 409)

    def test_barcode_duplicates_and_controls_rejected_atomically(self):
        for barcode in ("000123456789", "bad\x00code", "a" * 129, 123, True):
            before = self.store.summary()
            with self.subTest(barcode=barcode), self.assertRaises(InventoryError):
                self.store.import_rows([{"sku": "NEW", "quantity": 3, "barcode": barcode}])
            self.assertEqual(self.store.summary(), before)
        for value in ("", "\x00", 123, "a" * 129):
            with self.assertRaises(InventoryError):
                self.store.resolve_code(value)

    def test_barcode_swap_preserves_audit_original_values_and_omission(self):
        self.store.import_rows([{"sku": "OTHER", "quantity": 2, "barcode": "999"}])
        self.store.import_rows([{"sku": "FILTER-1", "quantity": 10, "barcode": "999"},
                                {"sku": "OTHER", "quantity": 2, "barcode": "000123456789"}])
        self.assertEqual(self.store.resolve_code("999")["sku"], "FILTER-1")
        history = self.store.movements("FILTER-1")["movements"]
        self.assertEqual(history[0]["changes"]["barcode"], {"before": "000123456789", "after": "999"})
        self.store.import_rows([{"sku": "FILTER-1", "quantity": 10}])
        self.assertEqual(self.store.get("FILTER-1")["barcode"], "999")
        self.assertEqual(self.store.movements("FILTER-1")["movements"], history)

    def test_csv_upc_mapping_and_duplicate_detection(self):
        path = self.root / "parts.csv"
        path.write_text("SKU,Qty,UPC\nA,2,000000123456\nB,1,000000123456\n")
        plan = parse_excel(path)
        self.assertEqual(plan.rows[0]["barcode"], "000000123456")
        self.assertFalse(plan.valid)
        self.assertIn("Duplicate barcode", plan.errors[0])

    def test_excel_zero_format_and_text_ean_preserved_large_numeric_rejected(self):
        import openpyxl
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.append(["SKU", "Qty", "EAN"])
        sheet.append(["A", 1, 123456])
        sheet["C2"].number_format = "0000000000000"
        sheet.append(["B", 1, "000123456789012345"])
        sheet.append(["C", 1, 1234567890123456])
        sheet["C4"].number_format = "0000000000000000"
        path = self.root / "stock.xlsx"
        book.save(path)
        plan = parse_excel(path)
        self.assertEqual([row["barcode"] for row in plan.rows], ["0000000123456", "000123456789012345"])
        self.assertFalse(plan.valid)
        self.assertIn("as text", plan.errors[0])

    def test_all_movement_types_update_quantities_and_low_stock(self):
        self.assertEqual(self.move("receive", 3)["item"]["quantity"], 13)
        self.assertEqual(self.move("sale", 11)["item"]["quantity"], 2)
        result = self.move("return", 1)
        self.assertTrue(result["item"]["low_stock"])
        self.assertEqual(result["movement"]["delta"], 1)
        result = self.move("count", 0, reason="Shelf counted", reference="Cycle 17")
        self.assertEqual(result["movement"]["before_quantity"], 3)
        self.assertEqual(result["movement"]["after_quantity"], 0)
        self.assertEqual(result["movement"]["delta"], -3)
        self.assertEqual(result["summary"]["total_quantity"], 0)
        self.assertEqual(result["movement"]["reference"], "Cycle 17")

    def test_invalid_quantities_negative_stock_and_overflow_leave_no_audit(self):
        before = self.store.movements()
        for kind, quantity, reason in (("sale", 11, ""), ("receive", COUNT_LIMIT, ""),
                                       ("sale", 0, ""), ("return", -1, ""), ("receive", True, ""),
                                       ("sale", 1.5, ""), ("count", 0, ""), ("count", COUNT_LIMIT + 1, "Count"),
                                       ("delete", 1, "")):
            with self.subTest(kind=kind, quantity=quantity), self.assertRaises(InventoryError):
                self.move(kind, quantity, reason=reason)
        self.assertEqual(self.store.get("FILTER-1")["quantity"], 10)
        self.assertEqual(self.store.movements(), before)

    def test_conflicts_use_part_revision_not_unrelated_catalogue_revision(self):
        revision = self.store.get("FILTER-1")["revision"]
        self.store.import_rows([{"sku": "OTHER", "quantity": 1}])
        self.move(expected_revision=revision)
        with self.assertRaises(InventoryError) as caught:
            self.move(expected_revision=revision)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.store.get("FILTER-1")["quantity"], 9)

    def test_retries_survive_reopen_and_reject_changed_payload(self):
        request_id = uuid.uuid4().hex
        revision = self.store.get("FILTER-1")["revision"]
        first = self.move(request_id=request_id, expected_revision=revision)
        self.store = InventoryStore(self.store.path)
        self.assertEqual(self.move(request_id=request_id, expected_revision=revision), first)
        with self.assertRaises(InventoryError) as caught:
            self.move(quantity=2, request_id=request_id, expected_revision=revision)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.store.get("FILTER-1")["quantity"], 9)
        self.assertEqual(len(self.store.movements()["movements"]), 2)

    def test_concurrent_sales_cannot_overwrite_each_other(self):
        revision = self.store.get("FILTER-1")["revision"]
        barrier = threading.Barrier(2)
        def sale():
            barrier.wait()
            try:
                return self.move(quantity=7, expected_revision=revision)["item"]["quantity"]
            except InventoryError as exc:
                return exc.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: sale(), range(2)))
        self.assertEqual(sorted(results), [3, 409])
        self.assertEqual(self.store.get("FILTER-1")["quantity"], 3)

    def test_concurrent_duplicate_retries_commit_only_one_movement(self):
        revision, request_id = self.store.get("FILTER-1")["revision"], uuid.uuid4().hex
        barrier = threading.Barrier(2)
        def sale():
            barrier.wait()
            return self.move(expected_revision=revision, request_id=request_id)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: sale(), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.store.get("FILTER-1")["quantity"], 9)
        self.assertEqual(len(self.store.movements()["movements"]), 2)

    def test_import_changes_audited_but_cache_snapshot_does_not_create_history(self):
        self.store.import_rows([{"sku": "FILTER-1", "quantity": 7, "location": "B-4"}], source="Manual stock edit")
        latest = self.store.movements()["movements"][0]
        self.assertEqual(latest["delta"], -3)
        self.assertEqual(latest["changes"]["location"], {"before": "A-2", "after": "B-4"})
        cache = InventoryStore(self.root / "cache.sqlite")
        cache.replace_snapshot(self.store.search(), self.store.summary()["revision"])
        self.assertEqual(cache.movements()["movements"], [])
        self.assertEqual(cache.resolve_code("000123456789")["quantity"], 7)

    def test_history_keyset_pagination_does_not_repeat_when_new_records_arrive(self):
        for _ in range(5):
            self.move("receive")
        page = self.store.movements("filter-1", limit=2)
        self.move("receive")
        second = self.store.movements("FILTER-1", limit=2, before_id=page["next_before_id"])
        third = self.store.movements("FILTER-1", limit=2, before_id=second["next_before_id"])
        ids = [row["id"] for part in (page, second, third) for row in part["movements"]]
        self.assertEqual(ids, [6, 5, 4, 3, 2, 1])
        self.assertFalse(third["has_more"])
        self.assertIsNone(third["next_before_id"])

    def test_long_unicode_audits_page_by_bytes_without_losing_any_records(self):
        for index in range(50):
            text = "\U0001f527" if index % 2 else "\U0001f698"
            self.store.import_rows([{"sku": "FILTER-1", "quantity": 10, "description": text * 2000,
                                    "location": text * 500, "supplier": text * 500}])
        page = self.store.movements(limit=50)
        self.assertLess(len(page["movements"]), 50)
        ids = []
        while True:
            self.assertLess(len(json.dumps(page, ensure_ascii=False).encode("utf-8")), 1024 * 1024)
            ids.extend(row["id"] for row in page["movements"])
            if not page["has_more"]:
                break
            page = self.store.movements(limit=50, before_id=page["next_before_id"])
        self.assertEqual(ids, list(range(51, 0, -1)))

    def test_pre_barcode_database_migrates_without_changing_existing_stock(self):
        path = self.root / "legacy.sqlite"
        with sqlite3.connect(path) as conn:
            conn.execute("""CREATE TABLE inventory_items(key TEXT PRIMARY KEY,sku TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',quantity INTEGER NOT NULL,location TEXT NOT NULL DEFAULT '',
                reorder_point INTEGER NOT NULL DEFAULT 0,reorder_quantity INTEGER NOT NULL DEFAULT 0,
                target_stock INTEGER,supplier TEXT NOT NULL DEFAULT '',revision INTEGER NOT NULL,
                updated_at TEXT NOT NULL,source TEXT NOT NULL)""")
            conn.execute("INSERT INTO inventory_items(key,sku,quantity,revision,updated_at,source) VALUES('old','OLD',4,0,'','')")
        migrated = InventoryStore(path)
        self.assertEqual(migrated.resolve_code("OLD")["quantity"], 4)
        self.assertEqual(migrated.get("OLD")["barcode"], "")
        self.assertEqual(migrated.movements()["movements"], [])
        migrated.import_rows([{"sku": "OLD", "quantity": 4, "barcode": "0004"}])
        self.assertEqual(migrated.resolve_code("0004")["sku"], "OLD")


if __name__ == "__main__":
    unittest.main()
