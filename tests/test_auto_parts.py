"""Famous Twins order amounts, sourcing lists and durable business metadata."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from auto_parts import (CHECKLIST_TEMPLATES, MAX_PAYMENT_CENTS, MAX_UNIT_PRICE_CENTS,
                        STOCK_STATUSES, customer_history, dashboard_summary,
                        format_money, money_to_cents, order_totals, parts_to_source)
from notebook_backup import export_backup, prepare_backup
from notes import BUSINESS_FIELDS, NoteStore, business_details, business_summary, note_fingerprint, note_search_text


class MoneyTests(unittest.TestCase):
    def test_decimal_amounts_are_exact_without_rounding(self):
        for text, expected in [('0', 0), ('0.01', 1), ('12.3', 1230), ('00012.30', 1230),
                               (' 999999999999.99 ', MAX_PAYMENT_CENTS - 1),
                               ('1000000000000.00', MAX_PAYMENT_CENTS)]:
            with self.subTest(text=text):
                self.assertEqual(money_to_cents(text), expected)
                self.assertIs(type(money_to_cents(text)), int)

    def test_ambiguous_or_unsupported_amounts_are_rejected(self):
        values = ['', ' ', '-1', '+1', '1,000', 'JMD 10', '$10', '1e2', '0.001',
                  '.50', '1.', '1.2.3', 'NaN', 'Infinity', '１２.００', True, 12, 1.2, None,
                  '1000000000000.01', '9' * 10000]
        for value in values:
            with self.subTest(value=str(value)[:40]):
                with self.assertRaises(ValueError):
                    money_to_cents(value)

    def test_formatting_never_loses_large_integer_cents(self):
        self.assertEqual(format_money(123450), 'JMD 1,234.50')
        self.assertEqual(format_money(9_007_199_254_740_993), 'JMD 90,071,992,547,409.93')
        for value in (True, -1, 1.2, '100', None):
            self.assertEqual(format_money(value), 'JMD 0.00')

    def test_totals_cover_quantities_partial_payments_and_credit(self):
        note = {'checklist': [{'text': 'Pads', 'quantity': 2, 'unit_price_cents': 12345},
                              {'text': 'Filter', 'quantity': 3, 'unit_price_cents': 1001, 'done': True}],
                'payment_received_cents': 10000}
        self.assertEqual(order_totals(note), {'subtotal_cents': 27693, 'paid_cents': 10000,
                                            'balance_cents': 17693, 'credit_cents': 0})
        note['payment_received_cents'] = 30000
        self.assertEqual(order_totals(note)['credit_cents'], 2307)
        self.assertEqual(order_totals(note)['balance_cents'], 0)

    def test_invalid_raw_amounts_cannot_make_negative_or_boolean_balances(self):
        for value in (True, False, -1, '123', 10.5, None, {}, []):
            with self.subTest(value=value):
                note = {'checklist': [{'quantity': 2, 'unit_price_cents': value}],
                        'payment_received_cents': value}
                self.assertEqual(order_totals(note), {'subtotal_cents': 0, 'paid_cents': 0,
                                                    'balance_cents': 0, 'credit_cents': 0})
        note = {'checklist': [{'quantity': 999999999, 'unit_price_cents': 10**100}],
                'payment_received_cents': 10**100}
        totals = order_totals(note)
        self.assertEqual(totals['subtotal_cents'], 9999 * MAX_UNIT_PRICE_CENTS)
        self.assertEqual(totals['paid_cents'], MAX_PAYMENT_CENTS)

    def test_legacy_unpriced_checklist_still_has_zero_totals(self):
        self.assertEqual(order_totals({'checklist': [{'text': 'Old part', 'quantity': 2, 'done': False}]}),
                         {'subtotal_cents': 0, 'paid_cents': 0, 'balance_cents': 0, 'credit_cents': 0})


class AutoPartsStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.store = NoteStore(self.root / 'notes.json')
        self.stores = [self.store]
        self.details = {
            'kind': 'order', 'customer': 'Famous Twins customer', 'contact': '876-555-0100',
            'order_ref': 'FT-123', 'order_status': 'preparing', 'order_due': 500,
            'vehicle': '2015 Toyota Corolla 1.8', 'registration': '1234 AB',
            'vin': 'MANUALLY ENTERED VIN', 'priority': 'urgent', 'order_type': 'order',
            'currency': 'JMD', 'payment_received_cents': 10000,
            'checklist': [{'id': 'pads', 'text': 'Front brake pads', 'quantity': 2, 'done': False,
                           'part_number': 'PAD-123', 'supplier': 'Parts Supplier', 'bin_location': 'A-12',
                           'unit_price_cents': 12345, 'stock_status': 'to_order'}],
        }

    def tearDown(self):
        for store in self.stores:
            store.close()
        self.directory.cleanup()

    def order(self, **changes):
        return self.store.add('Corolla order', 'Check fitment before ordering.', now=100,
                              repeat=0, details={**copy.deepcopy(self.details), **changes})

    def target(self):
        store = NoteStore(self.root / 'target' / 'notes.json')
        self.stores.append(store)
        return store

    def test_order_and_rich_part_fields_persist_and_edit(self):
        original = self.order()
        reopened = NoteStore(self.store.path)
        self.stores.append(reopened)
        restored = reopened.find(original['id'])
        self.assertEqual(business_details(restored), business_details(original))
        self.assertEqual(restored['vin'], 'MANUALLY ENTERED VIN')
        self.assertEqual(restored['checklist'][0]['unit_price_cents'], 12345)
        self.store.modify(original['id'], order_type='quote', payment_received_cents=123)
        self.assertEqual(original['order_type'], 'quote')
        self.assertEqual(original['payment_received_cents'], 123)

    def test_legacy_records_get_safe_defaults_without_losing_existing_work(self):
        legacy = {'id': 'legacy', 'title': 'Legacy order', 'body': 'Keep these instructions',
                  'kind': 'order', 'customer': 'Existing customer', 'order_status': 'ready',
                  'checklist': [{'id': 'old', 'text': 'Filter', 'quantity': 3, 'done': True}]}
        self.store.path.write_text(json.dumps({'version': 1, 'notes': [legacy]}), encoding='utf-8')
        reopened = NoteStore(self.store.path)
        self.stores.append(reopened)
        note = reopened.find('legacy')
        self.assertEqual(note['body'], 'Keep these instructions')
        self.assertEqual(note['customer'], 'Existing customer')
        self.assertEqual(note['order_status'], 'ready')
        self.assertTrue(note['checklist'][0]['done'])
        self.assertEqual(note['checklist'][0]['quantity'], 3)
        self.assertEqual(note['checklist'][0]['unit_price_cents'], 0)
        self.assertEqual(note['checklist'][0]['stock_status'], 'check_stock')
        self.assertEqual((note['currency'], note['order_type'], note['priority'], note['payment_received_cents']),
                         ('JMD', 'order', 'normal', 0))

    def test_invalid_fields_and_cents_are_bounded(self):
        for value in (True, False, -1, 1.5, '123', None, [], {}):
            note = self.order(currency='USD', priority='fast', order_type='invoice',
                              payment_received_cents=value,
                              checklist=[{'text': 'Part', 'quantity': True, 'unit_price_cents': value,
                                          'stock_status': 'automatic'}])
            self.assertEqual(note['payment_received_cents'], 0)
            self.assertEqual(note['checklist'][0]['unit_price_cents'], 0)
            self.assertEqual(note['checklist'][0]['quantity'], 1)
            self.assertEqual(note['checklist'][0]['stock_status'], 'check_stock')
            self.assertEqual((note['currency'], note['priority'], note['order_type']), ('JMD', 'normal', 'order'))
        note = self.order(vehicle='v' * 300, registration='r' * 100, vin='n' * 100,
                          payment_received_cents=10**100,
                          checklist=[{'text': 'Part', 'part_number': 'p' * 200, 'supplier': 's' * 200,
                                      'bin_location': 'b' * 200, 'unit_price_cents': 10**100}])
        self.assertEqual([len(note[key]) for key in ('vehicle', 'registration', 'vin')], [160, 32, 32])
        self.assertEqual([len(note['checklist'][0][key]) for key in ('part_number', 'supplier', 'bin_location')], [80, 100, 60])
        self.assertEqual(note['payment_received_cents'], MAX_PAYMENT_CENTS)
        self.assertEqual(note['checklist'][0]['unit_price_cents'], MAX_UNIT_PRICE_CENTS)

    def test_every_business_field_and_part_field_invalidates_guidance_fingerprint(self):
        note = self.order()
        baseline = note_fingerprint(note)
        changes = {'vehicle': 'Other vehicle', 'registration': 'OTHER', 'vin': 'Other VIN',
                   'priority': 'normal', 'order_type': 'quote', 'currency': 'OTHER', 'payment_received_cents': 999}
        for field, value in changes.items():
            with self.subTest(field=field):
                modified = copy.deepcopy(note)
                modified[field] = value
                self.assertNotEqual(note_fingerprint(modified), baseline)
                self.assertIn(field, BUSINESS_FIELDS)
        for field, value in {'part_number': 'OTHER', 'supplier': 'Other supplier', 'bin_location': 'Z-99',
                             'unit_price_cents': 1, 'stock_status': 'picked'}.items():
            modified = copy.deepcopy(note)
            modified['checklist'][0][field] = value
            self.assertNotEqual(note_fingerprint(modified), baseline)

    def test_search_includes_manual_vehicle_and_part_details(self):
        note = self.order()
        text = note_search_text(note)
        for value in ('2015 Toyota Corolla 1.8', '1234 AB', 'MANUALLY ENTERED VIN', 'urgent',
                      'JMD', 'PAD-123', 'Parts Supplier', 'A-12', 'to_order'):
            self.assertIn(value, text)
            self.assertIn(note['id'], self.store.search_ids(value))

    def test_json_backup_round_trip_keeps_prices_payments_and_manual_status(self):
        note = self.order()
        target = self.target()
        self.assertEqual(target.import_backup(self.store.export_json()), 1)
        self.assertEqual(business_details(target.find(note['id'])), business_details(note))
        self.assertEqual(order_totals(target.find(note['id'])), order_totals(note))

    def test_zip_backup_round_trip_preserves_rich_business_metadata(self):
        note = self.order()
        path = self.root / 'famous-twins.zip'
        export_backup(self.store, path)
        target = self.target()
        prepared = prepare_backup({'store': target, 'path': path})
        target.import_backup(prepared['payload'], allow_documents=True)
        self.assertEqual(business_details(target.find(note['id'])), business_details(note))

    def test_dashboard_excludes_terminal_orders_and_quotes_from_receivables(self):
        active = self.order()
        self.order(order_status='ready', priority='normal', order_due=800)
        self.order(order_status='delivered')
        self.order(order_status='cancelled')
        self.order(order_type='quote')
        self.store.add('Writing', 'A normal note', now=100, repeat=0)
        result = dashboard_summary(self.store.notes, now=600)
        self.assertEqual(result, {'open_orders': 2, 'ready_orders': 1, 'late_orders': 1,
                                  'urgent_orders': 1, 'quotes': 1, 'balance_cents': 29380,
                                  'parts_to_source': 4})
        self.assertEqual(business_summary(self.store.notes, 600)['balance_cents'], 29380)
        self.store.complete(active['id'])
        self.assertEqual(dashboard_summary(self.store.notes, 600)['open_orders'], 1)

    def test_sourcing_aggregates_requests_and_keeps_each_customers_order(self):
        first = self.order()
        second = self.order(customer='Other customer', order_ref='FT-124')
        self.order(order_type='quote')
        self.order(order_status='delivered')
        for status in ('in_stock', 'picked'):
            details = copy.deepcopy(self.details['checklist'])
            details[0]['stock_status'] = status
            self.order(checklist=details)
        checked = copy.deepcopy(self.details['checklist'])
        checked[0]['done'] = True
        self.order(checklist=checked)
        groups = parts_to_source(self.store.notes)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]['quantity'], 4)
        self.assertEqual(groups[0]['part_number'], 'PAD-123')
        self.assertEqual({order['id'] for order in groups[0]['orders']}, {first['id'], second['id']})
        self.assertEqual({order['customer'] for order in groups[0]['orders']},
                         {'Famous Twins customer', 'Other customer'})
        self.assertTrue(all(order['quantity'] == 2 for order in groups[0]['orders']))

    def test_sourcing_keeps_different_manual_statuses_and_suppliers_separate(self):
        for status, supplier in [('check_stock', 'A'), ('to_order', 'A'), ('ordered', 'A'), ('ordered', 'B')]:
            row = copy.deepcopy(self.details['checklist'][0])
            row.update(stock_status=status, supplier=supplier)
            self.order(checklist=[row])
        groups = parts_to_source(self.store.notes)
        self.assertEqual(len(groups), 4)
        self.assertEqual(sum(row['quantity'] for row in groups), 8)
        self.assertEqual(set(STOCK_STATUSES), {'check_stock', 'in_stock', 'to_order', 'ordered', 'picked'})

    def test_customer_history_includes_terminal_orders_and_quotes_without_bodies(self):
        first = self.order()
        terminal = self.order(order_status='delivered', customer='Other customer')
        quote = self.order(order_type='quote')
        self.store.modify(quote['id'], created=200)
        self.store.add('Unrelated', 'Private journal entry', now=300, repeat=0)
        rows = customer_history(self.store.notes, 'corolla')
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]['id'], quote['id'])
        self.assertEqual({row['id'] for row in customer_history(self.store.notes, 'Famous Twins')},
                         {first['id'], quote['id']})
        self.assertEqual(customer_history(self.store.notes, 'unknown customer'), [])
        self.assertTrue(next(row for row in rows if row['id'] == terminal['id'])['done'])
        self.assertTrue(all('body' not in row and 'checklist' not in row for row in rows))
        self.assertEqual(rows[0]['balance_cents'], 14690)

    def test_helpers_bound_history_and_preserve_large_portfolio_totals(self):
        note = self.order(payment_received_cents=0,
                          checklist=[{'text': 'Part', 'quantity': 9999, 'unit_price_cents': 1_000_000_000}
                                     for _ in range(100)])
        records = [note] * 2001
        self.assertEqual(len(customer_history(records)), 2000)
        summary = dashboard_summary(records, 600)
        self.assertEqual(summary['open_orders'], 2000)
        self.assertEqual(summary['balance_cents'], 1_999_800_000_000_000_000)
        self.assertIs(type(summary['balance_cents']), int)

    def test_templates_are_business_steps_and_do_not_pretend_to_adjust_stock(self):
        self.assertEqual(set(CHECKLIST_TEMPLATES), {'Opening', 'Closing', 'Parts handover'})
        self.assertTrue(all(isinstance(steps, tuple) and len(steps) >= 4
                            and all(isinstance(step, str) and 0 < len(step) <= 200 for step in steps)
                            for steps in CHECKLIST_TEMPLATES.values()))


if __name__ == '__main__':
    unittest.main()
