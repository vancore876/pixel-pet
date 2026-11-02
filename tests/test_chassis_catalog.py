"""Local VIN/frame list membership; not vehicle decoding or fitment."""
from pathlib import Path
import tempfile
import unittest

from chassis_catalog import ChassisCatalog, ChassisCatalogError, normalize_number


class ChassisCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.catalog = ChassisCatalog(self.root / 'chassis.sqlite')

    def tearDown(self):
        self.temp.cleanup()

    def write(self, value):
        path = self.root / 'list.txt'
        path.write_text(value, encoding='utf-8-sig')
        return path

    def test_imports_short_chassis_and_long_vin_as_opaque_identifiers(self):
        result = self.catalog.import_text(self.write('FRAME-00123\n1ABCD2345FG678901\nAB001234'))
        self.assertEqual(result, {'added': 3, 'total': 3, 'entries': 3})
        self.assertTrue(self.catalog.contains('frame00123'))
        self.assertTrue(self.catalog.contains('FRAME-00123'))
        self.assertTrue(self.catalog.contains('1ABCD2345FG678901'))
        self.assertTrue(self.catalog.contains('AB001234'))
        self.assertFalse(self.catalog.contains('FRAME00124'))
        self.assertFalse(self.catalog.contains('00123'))

    def test_second_import_merges_and_deduplicates_without_removing_prior_list(self):
        self.catalog.import_text(self.write('FRAME00123\nAB001234'))
        result = self.catalog.import_text(self.write('frame-00123;AB005678,AB005678'))
        self.assertEqual(result, {'added': 1, 'total': 3, 'entries': 3})
        self.assertTrue(self.catalog.contains('AB001234'))
        self.assertTrue(self.catalog.contains('AB005678'))

    def test_invalid_import_is_atomic_and_cannot_clear_existing_numbers(self):
        self.catalog.import_text(self.write('FRAME00123'))
        for text in ('AB005678 tiny', 'AB005678\ninvalid!value', ''):
            with self.subTest(text=text):
                with self.assertRaises(ChassisCatalogError):
                    self.catalog.import_text(self.write(text))
                self.assertEqual(self.catalog.count(), 1)
                self.assertFalse(self.catalog.contains('AB005678'))

    def test_reopen_persists_list_without_decoding(self):
        self.catalog.import_text(self.write('AB001234'))
        reopened = ChassisCatalog(self.root / 'chassis.sqlite')
        self.assertEqual(reopened.count(), 1)
        self.assertTrue(reopened.contains('ab001234'))
        self.assertIsNone(normalize_number('AB00 1234'))
        self.assertIsNone(normalize_number('AB001234; DROP TABLE chassis_numbers'))


if __name__ == '__main__':
    unittest.main()
