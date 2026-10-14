"""Readable and portable business backups retain part details and exact JMD."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from notebook_backup import export_backup, export_text
from notes import NoteStore


class BusinessExportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.store = NoteStore(self.root / 'notes.json')
        self.details = {
            'kind': 'order', 'customer': 'Counter customer', 'contact': '876-555-0100',
            'order_ref': 'FT-008', 'order_status': 'ready', 'order_type': 'order',
            'vehicle': '2015 Toyota Corolla 1.8', 'registration': '1234 AB',
            'vin': 'MANUALLY RECORDED VIN', 'priority': 'urgent', 'currency': 'JMD',
            'payment_received_cents': 10000,
            'checklist': [
                {'id': 'pads', 'text': 'Brake pads', 'quantity': 2, 'done': False,
                 'part_number': 'PAD-123', 'supplier': 'Parts Supplier', 'bin_location': 'A-12',
                 'unit_price_cents': 12345, 'stock_status': 'to_order'},
                {'id': 'filter', 'text': 'Air filter', 'quantity': 3, 'done': True,
                 'part_number': 'FLT-456', 'unit_price_cents': 1001, 'stock_status': 'picked'},
            ],
        }

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def add_order(self, **changes):
        return self.store.add('Customer pickup', 'Confirm fitment at the counter.', repeat=0,
                              details={**self.details, **changes})

    def text(self):
        path = self.root / 'notebook.txt'
        export_text({'store': self.store, 'path': path})
        return path.read_text(encoding='utf-8')

    def test_readable_order_export_includes_rich_fields_and_exact_totals(self):
        self.add_order()
        text = self.text()
        for value in ('Record type: Order', 'Order status: ready', 'Customer: Counter customer',
                      'Order reference: FT-008', 'Vehicle: 2015 Toyota Corolla 1.8',
                      'Registration: 1234 AB', 'VIN: MANUALLY RECORDED VIN', 'Priority: Urgent',
                      '[ ] 2 x Brake pads | Part number: PAD-123 | Supplier: Parts Supplier | Bin: A-12',
                      'Manual stock status: to order | Unit price: JMD 123.45',
                      '[x] 3 x Air filter | Part number: FLT-456', 'Unit price: JMD 10.01',
                      'Subtotal: JMD 276.93', 'Payment recorded: JMD 100.00',
                      'Balance: JMD 176.93', 'Credit: JMD 0.00'):
            with self.subTest(value=value):
                self.assertIn(value, text)

    def test_quote_and_overpayment_are_explicit_without_negative_balance(self):
        self.add_order(order_type='quote', payment_received_cents=30000)
        text = self.text()
        self.assertIn('Record type: Quote', text)
        self.assertIn('Balance: JMD 0.00', text)
        self.assertIn('Credit: JMD 23.07', text)
        self.assertNotIn('JMD -', text)

    def test_archive_json_keeps_all_cents_and_rich_fields(self):
        original = self.add_order()
        path = self.root / 'notebook.zip'
        export_backup(self.store, path)
        with zipfile.ZipFile(path) as archive:
            restored = json.loads(archive.read('notebook.json'))['notes'][0]
        for field in ('vehicle', 'registration', 'vin', 'priority', 'order_type', 'currency',
                      'payment_received_cents', 'checklist'):
            self.assertEqual(restored[field], original[field])
        self.assertIsInstance(restored['payment_received_cents'], int)
        self.assertEqual(restored['checklist'][0]['unit_price_cents'], 12345)

    def test_plain_checklist_preserves_original_lines_without_order_totals(self):
        self.store.add('Opening list', 'Counter preparation.', repeat=0, details={
            'kind': 'list', 'checklist': [{'id': 'one', 'text': 'Review pickups', 'quantity': 1, 'done': True}]})
        text = self.text()
        self.assertIn('[x] 1 x Review pickups\n', text)
        self.assertNotIn('Subtotal:', text)
        self.assertNotIn('Unit price:', text)


if __name__ == '__main__':
    unittest.main()
