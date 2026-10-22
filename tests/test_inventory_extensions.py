"""Zero-stock imports, replacement references, exact prices and schema upgrades."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from inventory import InventoryStore, InventoryError, parse_excel, validate_row


class InventoryExtensionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def workbook(self):
        from openpyxl import Workbook
        path = self.root / 'prices.xlsx'
        book = Workbook()
        sheet = book.active
        sheet.append(['Part Number', 'Quantity', 'Location', 'AlternatePartNumber',
                      'SubstitutePartNumber', 'Retail Price', 'Cost Price'])
        sheet.append(['00042', 0, 'Rack A', 123, 'SUB-9', 1250.50, 800.25])
        sheet.cell(2, 4).number_format = '00000'
        sheet.append(['P-2', 3, 'Rack B', 'ALT-2', 'SUB-2', 0, 0])
        book.save(path)
        book.close()
        return path

    def test_excel_import_retains_zero_quantity_part_codes_and_prices(self):
        plan = parse_excel(self.workbook())
        self.assertTrue(plan.valid, plan.errors)
        self.assertEqual(plan.rows[0], {'sku': '00042', 'quantity': 0, 'location': 'Rack A',
            'alternate_part_number': '00123', 'substitute_part_number': 'SUB-9',
            'retail_price': '1250.50', 'cost_price': '800.25'})
        store = InventoryStore(self.root / 'stock.sqlite')
        store.import_rows(plan.rows)
        restored = InventoryStore(store.path)
        self.assertEqual(restored.get('00042')['retail_price'], '1250.50')
        self.assertEqual(restored.search('00123')[0]['sku'], '00042')
        self.assertEqual(restored.search('SUB-9')[0]['sku'], '00042')
        store.import_rows([{'sku': '00042', 'quantity': 2}])
        self.assertEqual(store.get('00042')['cost_price'], '800.25')
        self.assertEqual(store.get('00042')['alternate_part_number'], '00123')
        target = InventoryStore(self.root / 'cache.sqlite')
        target.replace_snapshot(store.search(), store.summary()['revision'])
        self.assertEqual(target.get('00042')['cost_price'], '800.25')

    def test_zero_quantity_option_can_exclude_and_reinclude_rows(self):
        path = self.workbook()
        plan = parse_excel(path, include_zero=False)
        self.assertTrue(plan.valid)
        self.assertEqual([row['sku'] for row in plan.rows], ['P-2'])
        self.assertIn('Skipped 1 zero-quantity rows', ' '.join(plan.warnings))
        self.assertEqual(len(parse_excel(path, include_zero=True).rows), 2)

    def test_prices_reject_invalid_values_without_changing_stock(self):
        for price in [-1, True, 'NaN', 'Infinity', '12.345', '12,34', '1000000000']:
            with self.subTest(price=price), self.assertRaises(InventoryError):
                validate_row({'sku': 'P-1', 'quantity': 0, 'cost_price': price})
        self.assertEqual(validate_row({'sku': 'P-1', 'quantity': 0,
            'retail_price': '$1,234.50', 'cost_price': 0})['retail_price'], '1234.50')

    def test_quantity_bypass_skips_bad_rows_without_changing_existing_stock(self):
        path = self.root / 'bad-quantities.csv'
        path.write_text('SKU,Quantity,Location\nGOOD,2,New bin\nNEG,-1,Wrong bin\nFRACTION,2.5,Wrong bin\nHUGE,2147483648,Wrong bin\nEMPTY,,Wrong bin\nTEXT,unknown,Wrong bin\nZERO,0,Zero bin\n', encoding='utf-8')
        self.assertFalse(parse_excel(path).valid)
        plan = parse_excel(path, skip_invalid_quantities=True)
        self.assertTrue(plan.valid, plan.errors)
        self.assertEqual([row['sku'] for row in plan.rows], ['GOOD', 'ZERO'])
        self.assertIn('Skipped 5 rows with invalid quantities', ' '.join(plan.warnings))
        self.assertIn('Row 3:', ' '.join(plan.warnings))
        store = InventoryStore(self.root / 'bypass.sqlite')
        store.import_rows([{'sku': 'NEG', 'quantity': 12, 'location': 'Keep bin'}])
        store.import_rows(plan.rows)
        self.assertEqual(store.get('NEG')['quantity'], 12)
        self.assertEqual(store.get('NEG')['location'], 'Keep bin')
        self.assertEqual(store.get('ZERO')['quantity'], 0)

    def test_quantity_bypass_does_not_hide_price_or_duplicate_errors(self):
        path = self.root / 'other-errors.csv'
        path.write_text('SKU,Qty,Cost Price\nA,2,1.234\nB,1,10\nB,1,10\n', encoding='utf-8')
        plan = parse_excel(path, skip_invalid_quantities=True)
        self.assertFalse(plan.valid)
        self.assertTrue(any('Cost Price' in error for error in plan.errors))
        self.assertTrue(any('Duplicate part number' in error for error in plan.errors))
        path.write_text('SKU,Qty\nA,-1\n', encoding='utf-8')
        plan = parse_excel(path, skip_invalid_quantities=True)
        self.assertFalse(plan.valid)
        self.assertEqual(plan.rows, [])
        self.assertIn('No valid stock rows remain', plan.errors[0])

    def test_invalid_price_bypass_imports_parts_and_preserves_existing_prices(self):
        path = self.root / 'bad-prices.csv'
        path.write_text('SKU,Qty,Location,Retail Price,Cost Price\nOLD,4,New bin,12.345,5.25\nNEW,0,Rack B,-1,NaN\nBADQ,-2,Ignored,-1,-1\n', encoding='utf-8')
        plan = parse_excel(path, skip_invalid_quantities=True, skip_invalid_prices=True)
        self.assertTrue(plan.valid, plan.errors)
        self.assertEqual(len(plan.rows), 2)
        self.assertNotIn('retail_price', plan.rows[0])
        self.assertEqual(plan.rows[0]['cost_price'], '5.25')
        self.assertNotIn('cost_price', plan.rows[1])
        self.assertIn('Skipped 3 invalid price values', ' '.join(plan.warnings))
        self.assertIn('Row 2:', ' '.join(plan.warnings))
        store = InventoryStore(self.root / 'prices.sqlite')
        store.import_rows([{'sku': 'OLD', 'quantity': 10, 'retail_price': '25.50', 'cost_price': '4.00'}])
        store.import_rows(plan.rows)
        self.assertEqual(store.get('OLD')['retail_price'], '25.50')
        self.assertEqual(store.get('OLD')['cost_price'], '5.25')
        self.assertEqual(store.get('OLD')['quantity'], 4)
        self.assertEqual(store.get('NEW')['retail_price'], '0.00')
        self.assertEqual(store.get('NEW')['quantity'], 0)

    def test_price_bypass_keeps_other_errors_and_strict_mode_available(self):
        path = self.root / 'price-errors.csv'
        path.write_text('SKU,Qty,Retail Price\nA,2,1.234\n', encoding='utf-8')
        self.assertFalse(parse_excel(path).valid)
        self.assertTrue(parse_excel(path, skip_invalid_prices=True).valid)
        path.write_text('SKU,Qty,Retail Price\nA,2,1.234\nA,3,-1\n', encoding='utf-8')
        plan = parse_excel(path, skip_invalid_prices=True)
        self.assertFalse(plan.valid)
        self.assertTrue(any('Duplicate part number' in error for error in plan.errors))

    def test_existing_database_upgrade_preserves_original_stock(self):
        path = self.root / 'legacy.sqlite'
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("CREATE TABLE inventory_items (key TEXT PRIMARY KEY, sku TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', quantity INTEGER NOT NULL, location TEXT NOT NULL DEFAULT '', reorder_point INTEGER NOT NULL DEFAULT 0, reorder_quantity INTEGER NOT NULL DEFAULT 0, target_stock INTEGER, supplier TEXT NOT NULL DEFAULT '', barcode TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL, updated_at TEXT NOT NULL, source TEXT NOT NULL)")
            connection.execute("INSERT INTO inventory_items(key,sku,quantity,revision,updated_at,source) VALUES('old','OLD',0,5,'saved','legacy')")
        store = InventoryStore(path)
        item = store.get('OLD')
        self.assertEqual(item['quantity'], 0)
        self.assertEqual(item['revision'], 5)
        self.assertEqual(item['retail_price'], '0.00')
        self.assertEqual(item['alternate_part_number'], '')
        store.import_rows([{'sku': 'OLD', 'quantity': 0, 'cost_price': '25.75'}])
        self.assertEqual(InventoryStore(path).get('OLD')['cost_price'], '25.75')
