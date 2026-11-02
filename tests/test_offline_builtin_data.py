"""Bundled catalogue setup must preserve a customer's existing data."""
from pathlib import Path
import tempfile
import unittest

from chassis_catalog import ChassisCatalog
from offline_builtin_data import ensure_builtin_data
from parts_catalog import PartsCatalog


class BuiltinDataTests(unittest.TestCase):
    def test_fresh_profile_loads_both_lists_and_restarts_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            result = ensure_builtin_data(profile)
            self.assertEqual(result['parts'], 46848)
            self.assertEqual(result['chassis_total'], 1014)
            self.assertTrue(ensure_builtin_data(profile)['already_loaded'])
            self.assertEqual(ChassisCatalog(profile / 'chassis.sqlite').count(), 1014)

    def test_existing_parts_and_extra_chassis_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            import csv
            from parts_catalog import FIELDS
            source = profile / 'custom.csv'
            with source.open('w', encoding='utf-8', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(FIELDS))
                writer.writeheader()
                writer.writerow({**{name: '' for name in FIELDS}, 'PartNumber': 'CUSTOM99', 'Description': 'Custom item'})
            parts = PartsCatalog(profile / 'parts-catalog.sqlite')
            parts.import_csv(source)
            extra = profile / 'extra.txt'
            extra.write_text('EXTRA987654321\n', encoding='utf-8')
            ChassisCatalog(profile / 'chassis.sqlite').import_text(extra)
            ensure_builtin_data(profile)
            self.assertEqual(parts.metadata()['row_count'], 1)
            self.assertEqual(parts.search('CUSTOM99')[0]['description'], 'Custom item')
            self.assertTrue(ChassisCatalog(profile / 'chassis.sqlite').contains('EXTRA987654321'))


if __name__ == '__main__':
    unittest.main()
