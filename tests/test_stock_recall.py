import unittest
from stock_recall import requested_stock


class StockRecallTests(unittest.TestCase):
    def test_requests_distinguish_shortage_absence_and_ambiguity(self):
        catalog = [{'sku': 'P1', 'quantity': 0, 'alternate_part_number': 'ALT1'},
                   {'sku': 'P2', 'quantity': 2, 'description': 'Pads'},
                   {'sku': 'P3', 'quantity': 10, 'alternate_part_number': 'DUP'},
                   {'sku': 'P4', 'quantity': 5, 'alternate_part_number': 'DUP'}]
        notes = [{'id': 'order', 'title': 'Customer request', 'checklist': [
            {'text': 'Filter', 'part_number': 'ALT1', 'quantity': 1},
            {'text': 'Pads', 'part_number': 'P2', 'quantity': 4},
            {'text': 'Unknown', 'part_number': 'NOT-FOUND'},
            {'text': 'Ambiguous', 'part_number': 'DUP'},
            {'text': 'Completed', 'part_number': 'P1', 'done': True}]}]
        result = requested_stock(catalog, notes)
        self.assertEqual([row['status'] for row in result['requests']],
                         ['out_of_stock', 'insufficient', 'not_found', 'ambiguous'])
        self.assertEqual(result['requests'][1]['shortfall'], 2)
        self.assertEqual(result['requests'][0]['note_title'], 'Customer request')

    def test_writing_references_and_completed_notes(self):
        catalog = [{'sku': 'AB-001', 'quantity': 0}]
        notes = [{'id': 'writing', 'kind': 'note', 'body': 'Need AB-001 and AB-002'},
                 {'kind': 'note', 'body': 'AB-001', 'done': True}]
        report = requested_stock(catalog, notes)
        self.assertEqual(report['counts'], {'out_of_stock': 1, 'not_found': 1})
        self.assertEqual(report['request_count'], 2)

    def test_combines_demand_across_orders(self):
        stock = [{'sku': 'P1', 'quantity': 3}]
        notes = [{'checklist': [{'part_number': 'P1', 'quantity': 2}]} for _ in range(2)]
        report = requested_stock(stock, notes)
        self.assertEqual(report['combined_shortages'][0]['shortfall'], 1)
