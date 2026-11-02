"""Offline snapshot import and search never overwrite office inventory."""
import csv
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from parts_catalog import FIELDS, PartsCatalog, PartsCatalogError


class PartsCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.catalog = PartsCatalog(self.root / 'snapshot.sqlite')

    def write_csv(self, name, rows):
        path = self.root / name
        with path.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(FIELDS))
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_lookup_is_dated_preserves_raw_quantities_and_hides_costs(self):
        path = self.write_csv('parts-2026-10-22.csv', [
            {'PartNumber': '000-A1', 'Description': 'Brake pad Toyota Corolla',
             'AlternatePartNumber': 'X-123', 'StoreQuantity': '-1.2500',
             'RetailPrice': '100.0050', 'CostPrice': '40', 'WholeSalePrice': '50', 'StoreBin1': 'A3'},
            {'PartNumber': 'OTHER', 'Description': 'Brake pad Honda', 'WarehouseQuantity': ''},
        ])
        meta = self.catalog.import_csv(path)
        self.assertEqual((meta['row_count'], meta['source_date']), (2, '2026-10-22'))
        row, = self.catalog.search('000-A1')
        self.assertEqual(row['part_number'], '000-A1')
        self.assertEqual(row['store_quantity'], '-1.2500')
        self.assertEqual(row['warehouse_quantity'], '')
        self.assertEqual(row['retail_price'], '100.0050')
        self.assertEqual(row['store_bin1'], 'A3')
        self.assertNotIn('cost_price', row)
        self.assertNotIn('wholesale_price', row)
        self.assertEqual(self.catalog.search('toyota corolla')[0]['part_number'], '000-A1')
        self.assertEqual(self.catalog.search('X-123')[0]['part_number'], '000-A1')
        self.assertEqual(self.catalog.search('nonexistent'), [])
        self.assertEqual(self.catalog.search('" OR *'), [])

    def test_failed_replacement_does_not_destroy_previous_catalogue(self):
        self.catalog.import_csv(self.write_csv('old-2026-10-22.csv', [
            {'PartNumber': 'ABC', 'Description': 'Original part'}]))
        malformed = self.root / 'bad.csv'
        malformed.write_text('PartNumber,Description\nABC,Bad\n', encoding='utf8')
        with self.assertRaises(PartsCatalogError):
            self.catalog.import_csv(malformed)
        self.assertEqual(self.catalog.metadata()['source_date'], '2026-10-22')
        self.assertEqual(self.catalog.search('ABC')[0]['description'], 'Original part')

    def test_mid_import_error_rolls_back(self):
        self.catalog.import_csv(self.write_csv('old.csv', [{'PartNumber': 'ABC', 'Description': 'Old'}]))
        path = self.write_csv('new.csv', [{'PartNumber': 'DEF', 'Description': 'New'}])
        with path.open('a', encoding='utf8') as f:
            f.write('incomplete,row\n')
        with self.assertRaises(PartsCatalogError):
            self.catalog.import_csv(path)
        self.assertEqual(self.catalog.search('ABC')[0]['description'], 'Old')
        self.assertEqual(self.catalog.search('DEF'), [])

    def test_metadata_is_not_inferred_from_invalid_filename_date(self):
        self.catalog.import_csv(self.write_csv('parts-2026-02-31.csv', [{'PartNumber': 'ABC'}]))
        self.assertEqual(self.catalog.metadata()['source_date'], '')

    def test_scanner_exact_matches_preserve_ambiguity_and_do_not_expose_costs(self):
        self.catalog.import_csv(self.write_csv('parts.csv', [
            {'PartNumber': '00123', 'Description': 'First', 'CostPrice': '50'},
            {'PartNumber': 'PART-2', 'AlternatePartNumber': '00123', 'Description': 'Second', 'WholeSalePrice': '70'},
            {'PartNumber': '001234', 'SubstitutePartNumber': 'S-ABC', 'Description': 'Third'},
        ]))
        matches = self.catalog.exact_matches('00123')
        self.assertEqual({item['part_number'] for item in matches}, {'00123', 'PART-2'})
        self.assertTrue(all('cost_price' not in item and 'wholesale_price' not in item for item in matches))
        self.assertEqual(self.catalog.exact_matches('s-abc')[0]['part_number'], '001234')
        self.assertEqual(self.catalog.exact_matches('0123'), [])
        self.assertEqual(self.catalog.exact_matches(''), [])

    def test_lookup_date_and_rows_remain_consistent_during_replacement(self):
        old = self.write_csv('parts-2026-10-22.csv', [
            {'PartNumber': 'ABC', 'Description': 'Old description', 'StoreQuantity': '3'}])
        new = self.write_csv('parts-2026-11-01.csv', [
            {'PartNumber': 'ABC', 'Description': 'New description', 'StoreQuantity': '10'}])
        for method in ('lookup', 'lookup_exact'):
            with self.subTest(method=method):
                self.catalog.import_csv(old)
                writer = PartsCatalog(self.catalog.path)
                original = self.catalog._read_metadata

                def replace_after_read(db):
                    metadata = original(db)
                    # This commit occurs between the metadata query and the
                    # row query of the lookup on a separate connection.
                    writer.import_csv(new)
                    return metadata

                with patch.object(self.catalog, '_read_metadata', side_effect=replace_after_read):
                    metadata, rows = getattr(self.catalog, method)('ABC')
                self.assertEqual(metadata['source_date'], '2026-10-22')
                self.assertEqual(rows[0]['description'], 'Old description')
                self.assertEqual(rows[0]['store_quantity'], '3')
                current_metadata, current_rows = getattr(self.catalog, method)('ABC')
                self.assertEqual(current_metadata['source_date'], '2026-11-01')
                self.assertEqual(current_rows[0]['store_quantity'], '10')


if __name__ == '__main__':
    unittest.main()
