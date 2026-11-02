"""Chassis-specific mappings must not invent fitment or erase conflicts."""
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from parts_catalog import FIELDS, PartsCatalog
from vehicle_fitment import FitmentStore, candidate_parts


class VehicleFitmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = FitmentStore(self.root / 'local.sqlite')
        self.chassis = 'FRAME123456'

    def tearDown(self):
        self.temp.cleanup()

    def test_vehicle_and_part_association_are_exact_and_persistent(self):
        self.assertIsNone(self.db.get_vehicle(self.chassis))
        self.assertEqual(self.db.parts_for(self.chassis), [])
        with self.assertRaises(ValueError):
            self.db.add_part(self.chassis, 'PAD-1')
        self.db.save_vehicle(self.chassis, 'Toyota', 'Corolla', 2015, '1NZ', 'customer record')
        self.db.add_part(self.chassis, 'PAD-1', source='Checked against supplier')
        other = FitmentStore(self.db.path)
        vehicle, parts = other.fitment_view(self.chassis.lower())
        self.assertEqual((vehicle['make'], vehicle['model'], vehicle['year']), ('Toyota', 'Corolla', 2015))
        self.assertEqual(parts[0]['part_number'], 'PAD-1')
        self.assertTrue(parts[0]['confirmed_for_current_vehicle'])
        self.assertEqual(other.parts_for('FRAME999999'), [])

    def test_correcting_vehicle_invalidates_but_preserves_parts(self):
        self.db.save_vehicle(self.chassis, 'Toyota', 'Corolla', 2015)
        self.db.add_part(self.chassis, 'PAD-1')
        self.db.save_vehicle(self.chassis, 'Toyota', 'Corolla', 2016)
        self.assertFalse(self.db.parts_for(self.chassis)[0]['confirmed_for_current_vehicle'])
        self.db.add_part(self.chassis, 'PAD-1', source='Rechecked')
        self.assertTrue(self.db.parts_for(self.chassis)[0]['confirmed_for_current_vehicle'])
        self.db.save_vehicle(self.chassis, 'Toyota', 'Corolla', 2016, note='Updated note only')
        self.assertTrue(self.db.parts_for(self.chassis)[0]['confirmed_for_current_vehicle'])

    def test_candidates_use_exact_vehicle_tokens_and_never_infer_year(self):
        csv_path = self.root / 'parts.csv'
        with csv_path.open('w', encoding='utf8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(FIELDS))
            writer.writeheader()
            for sku, desc in [('FIT-PAD', 'DISC PAD HONDA FIT 2001-2013 CIVIC 2014-2018'),
                              ('FILTER', 'OIL FILTER HONDA ACCORD'), ('CIVIC-PAD', 'DISC PAD HONDA CIVIC 2014')]:
                writer.writerow({**{key: '' for key in FIELDS}, 'PartNumber': sku, 'Description': desc})
        catalog = PartsCatalog(self.root / 'parts.sqlite')
        catalog.import_csv(csv_path)
        metadata, rows = candidate_parts(catalog, 'Honda', 'FIT')
        self.assertEqual({row['part_number'] for row in rows}, {'FIT-PAD'})
        self.assertIn('CIVIC', rows[0]['description'])
        self.assertNotIn('year', rows[0])
        self.assertEqual(metadata['row_count'], 3)
        _, pads = candidate_parts(catalog, 'Honda', 'FIT', 'brake pads')
        self.assertEqual([row['part_number'] for row in pads], ['FIT-PAD'])
        with self.assertRaises(ValueError):
            candidate_parts(catalog, '', 'FIT')

    def test_import_conflict_is_atomic_and_never_overwrites(self):
        remote = FitmentStore(self.root / 'remote.sqlite')
        remote.save_vehicle('FRAME222222', 'Honda', 'Fit', 2013)
        remote.add_part('FRAME222222', 'P-2')
        remote.save_vehicle(self.chassis, 'Nissan', 'Note', 2017)
        file = self.root / 'shared.json'
        remote.export_to(file)
        self.db.save_vehicle(self.chassis, 'Toyota', 'Corolla', 2015)
        with self.assertRaisesRegex(ValueError, 'different vehicle'):
            self.db.import_from(file)
        self.assertEqual(self.db.list_vehicles()[0]['make'], 'Toyota')
        self.assertIsNone(self.db.get_vehicle('FRAME222222'))

    def test_stale_mapping_survives_multiple_pc_exports_and_reconfirmation(self):
        self.db.save_vehicle(self.chassis, 'Honda', 'Fit', 2012)
        self.db.add_part(self.chassis, 'PAD-1', source='Old check')
        self.db.save_vehicle(self.chassis, 'Honda', 'Fit', 2013)
        file1, file2 = self.root / 'one.json', self.root / 'two.json'
        self.db.export_to(file1)
        second = FitmentStore(self.root / 'second.sqlite')
        self.assertEqual(second.import_from(file1), {'vehicles': 1, 'parts': 1})
        self.assertFalse(second.parts_for(self.chassis)[0]['confirmed_for_current_vehicle'])
        second.export_to(file2)
        third = FitmentStore(self.root / 'third.sqlite')
        third.import_from(file2)
        self.assertFalse(third.parts_for(self.chassis)[0]['confirmed_for_current_vehicle'])
        third.add_part(self.chassis, 'PAD-1', source='Rechecked')
        self.assertTrue(third.parts_for(self.chassis)[0]['confirmed_for_current_vehicle'])

    def test_snapshot_vehicle_and_part_read_is_consistent_during_correction(self):
        self.db.save_vehicle(self.chassis, 'Honda', 'Fit', 2012)
        self.db.add_part(self.chassis, 'PAD-1')
        # Trigger another writer after vehicle SELECT but before part SELECT.
        original = self.db._connect
        writer = FitmentStore(self.db.path)
        class ConnectionProxy:
            def __init__(self, db): self.db, self.triggered = db, False
            def execute(self, sql, *args):
                result = self.db.execute(sql, *args)
                if sql.startswith('SELECT * FROM vehicles WHERE') and not self.triggered:
                    self.triggered = True
                    writer.save_vehicle('FRAME123456', 'Honda', 'Fit', 2013)
                return result
            def close(self): self.db.close()
        with patch.object(self.db, '_connect', side_effect=lambda: ConnectionProxy(original())):
            vehicle, parts = self.db.fitment_view(self.chassis)
        self.assertEqual(vehicle['year'], 2012)
        self.assertTrue(parts[0]['confirmed_for_current_vehicle'])
        self.assertFalse(self.db.parts_for(self.chassis)[0]['confirmed_for_current_vehicle'])

    def test_deletion_markers_prevent_old_export_from_restoring_a_part(self):
        self.db.save_vehicle(self.chassis, 'Honda', 'Fit', 2012)
        self.db.add_part(self.chassis, 'PAD-1')
        old = self.root / 'old.json'
        self.db.export_to(old)
        self.assertTrue(self.db.remove_part(self.chassis, 'PAD-1'))
        with self.assertRaisesRegex(ValueError, 'deleted on this PC'):
            self.db.import_from(old)
        self.assertEqual(self.db.parts_for(self.chassis), [])
        deleted = self.root / 'deleted.json'
        self.db.export_to(deleted)
        other = FitmentStore(self.root / 'second.sqlite')
        other.import_from(deleted)
        with self.assertRaisesRegex(ValueError, 'deleted on this PC'):
            other.import_from(old)
        other.add_part(self.chassis, 'PAD-1', source='Explicitly rechecked')
        self.assertTrue(other.parts_for(self.chassis)[0]['confirmed_for_current_vehicle'])

    def test_incoming_deletion_conflict_does_not_remove_local_mapping(self):
        self.db.save_vehicle(self.chassis, 'Honda', 'Fit', 2012)
        self.db.add_part(self.chassis, 'PAD-1')
        live = self.root / 'live.json'
        self.db.export_to(live)
        other = FitmentStore(self.root / 'second.sqlite')
        other.import_from(live)
        other.remove_part(self.chassis, 'PAD-1')
        deleted = self.root / 'deleted.json'
        other.export_to(deleted)
        with self.assertRaisesRegex(ValueError, 'deleted in the imported file'):
            self.db.import_from(deleted)
        self.assertTrue(self.db.parts_for(self.chassis)[0]['confirmed_for_current_vehicle'])

    def test_stale_edit_and_removal_tokens_cannot_overwrite_new_changes(self):
        original = self.db.save_vehicle(self.chassis, 'Honda', 'Fit', 2012, expected_updated_at=None)
        changed = self.db.save_vehicle(self.chassis, 'Honda', 'Fit', 2013,
                                       expected_updated_at=original['updated_at'])
        with self.assertRaisesRegex(ValueError, 'vehicle changed'):
            self.db.save_vehicle(self.chassis, 'Honda', 'Fit', 2014,
                                 expected_updated_at=original['updated_at'])
        with self.assertRaisesRegex(ValueError, 'vehicle changed'):
            self.db.add_part(self.chassis, 'PAD-1', expected_vehicle_updated_at=original['updated_at'])
        self.db.add_part(self.chassis, 'PAD-1', expected_vehicle_updated_at=changed['updated_at'],
                         expected_part_updated_at=None)
        before = self.db.parts_for(self.chassis)[0]
        self.db.add_part(self.chassis, 'PAD-1', source='Updated',
                         expected_part_updated_at=before['updated_at'])
        with self.assertRaisesRegex(ValueError, 'part mapping changed'):
            self.db.remove_part(self.chassis, 'PAD-1', expected_updated_at=before['updated_at'])
        self.assertEqual(self.db.parts_for(self.chassis)[0]['source'], 'Updated')


if __name__ == '__main__':
    unittest.main()
