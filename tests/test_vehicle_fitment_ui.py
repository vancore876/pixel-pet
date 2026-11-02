"""Vehicle mappings are explicit and catalogue guesses stay unconfirmed."""
import csv
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QMessageBox
from parts_catalog import FIELDS, PartsCatalog
from settings import AppSettings
from vehicle_fitment import FitmentStore
from vehicle_fitment_window import VehicleFitmentWindow

APP = QApplication.instance() or QApplication([])


class VehicleFitmentWindowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = AppSettings(self.root / 'settings.json')
        self.store = FitmentStore(self.root / 'vehicles.sqlite')
        self.catalog = PartsCatalog(self.root / 'parts.sqlite')
        source = self.root / 'parts-2026-01-01.csv'
        with source.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(FIELDS))
            writer.writeheader()
            writer.writerow({**{key: '' for key in FIELDS}, 'PartNumber': 'PAD-01',
                             'Description': 'DISC PAD TOY COROLLA', 'StoreBin1': 'A1'})
        self.catalog.import_csv(source)
        self.window = VehicleFitmentWindow(self.store, self.catalog, self.settings)

    def tearDown(self):
        self.window.shutdown()
        self.window.close()
        self.temp.cleanup()

    def save_vehicle(self):
        self.window.chassis.setText('JTDA123456789')
        self.window.make.setText('Toyota')
        self.window.model.setText('Corolla')
        self.window.year.setValue(2015)
        self.window.save_vehicle()
        self.assertIsNotNone(self.store.get_vehicle('JTDA123456789'))

    def test_candidate_never_becomes_confirmed_without_source_and_save(self):
        self.save_vehicle()
        self.window.find_candidates()
        self.assertEqual(self.window.candidates.count(), 1)
        self.assertIn('not verified', self.window.status.text())
        self.window.candidate_query.setText('brake pads')
        self.window.find_candidates()
        self.assertEqual(self.window.candidates.count(), 1)
        self.window.choose_candidate(self.window.candidates.item(0))
        self.assertEqual(self.window.part_number.text(), 'PAD-01')
        self.window.add_part()
        self.assertEqual(self.store.parts_for('JTDA123456789'), [])
        self.assertIn('source', self.window.status.text().lower())
        self.window.part_source.setText('Manufacturer catalogue page 12')
        self.window.add_part()
        row, = self.store.parts_for('JTDA123456789')
        self.assertTrue(row['confirmed_for_current_vehicle'])
        self.assertEqual(row['source'], 'Manufacturer catalogue page 12')

    def test_vehicle_correction_requires_saving_and_reconfirming_links(self):
        self.save_vehicle()
        self.window.part_number.setText('PAD-01')
        self.window.part_source.setText('Parts manual')
        self.window.add_part()
        self.window.year.setValue(2016)
        self.window.add_part()
        self.assertIn('Save the edited', self.window.status.text())
        self.window.save_vehicle()
        self.assertIn('Needs re-confirmation', self.window.linked.item(0).text())
        self.assertFalse(self.store.parts_for('JTDA123456789')[0]['confirmed_for_current_vehicle'])
        self.window.edit_link(self.window.linked.item(0))
        self.window.add_part()
        self.assertTrue(self.store.parts_for('JTDA123456789')[0]['confirmed_for_current_vehicle'])

    def test_export_import_and_conflicts_preserve_existing_data(self):
        self.save_vehicle()
        self.window.part_number.setText('PAD-01')
        self.window.part_source.setText('Parts manual')
        self.window.add_part()
        path = self.root / 'vehicle-mappings.json'
        with patch('vehicle_fitment_window.QFileDialog.getSaveFileName', return_value=(str(path), '')):
            self.window.export_mappings()
        self.assertTrue(path.exists())
        other = FitmentStore(self.root / 'other.sqlite')
        original = self.window.store
        self.window.store = other
        with patch('vehicle_fitment_window.QFileDialog.getOpenFileName', return_value=(str(path), '')):
            self.window.import_mappings()
        self.assertEqual(other.get_vehicle('JTDA123456789')['model'], 'Corolla')
        other.save_vehicle('JTDA123456789', 'Toyota', 'Camry', 2015)
        with patch('vehicle_fitment_window.QFileDialog.getOpenFileName', return_value=(str(path), '')):
            self.window.import_mappings()
        self.assertIn('preserved', self.window.status.text())
        self.assertEqual(other.get_vehicle('JTDA123456789')['model'], 'Camry')
        self.window.store = original

    def test_edit_and_remove_link_require_selection_and_confirmation(self):
        self.save_vehicle()
        self.window.part_number.setText('PAD-01')
        self.window.part_source.setText('Old manual')
        self.window.add_part()
        item = self.window.linked.item(0)
        self.window.edit_link(item)
        self.assertEqual(self.window.part_source.text(), 'Old manual')
        self.window.part_source.setText('Updated manual')
        self.window.add_part()
        self.assertEqual(self.store.parts_for('JTDA123456789')[0]['source'], 'Updated manual')
        self.window.linked.setCurrentRow(0)
        with patch('vehicle_fitment_window.QMessageBox.question', return_value=QMessageBox.No):
            self.window.remove_link()
        self.assertEqual(len(self.store.parts_for('JTDA123456789')), 1)
        with patch('vehicle_fitment_window.QMessageBox.question', return_value=QMessageBox.Yes):
            self.window.remove_link()
        self.assertEqual(self.store.parts_for('JTDA123456789'), [])
        self.assertIn('Older exported files', self.window.status.text())

    def test_changed_chassis_and_concurrent_vehicle_edit_do_not_overwrite(self):
        self.save_vehicle()
        self.window.chassis.setText('JTDA987654321')
        self.window.chassis.textEdited.emit('JTDA987654321')
        self.window.save_vehicle()
        self.assertIsNone(self.store.get_vehicle('JTDA987654321'))
        self.assertIn('Load this chassis', self.window.status.text())
        self.window.chassis.setText('JTDA123456789')
        self.window.load_vehicle()
        self.store.save_vehicle('JTDA123456789', 'Toyota', 'Camry', 2015)
        self.window.save_vehicle()
        self.assertEqual(self.store.get_vehicle('JTDA123456789')['model'], 'Camry')
        self.assertIn('changed', self.window.status.text().lower())

    def test_stale_part_editor_and_removal_do_not_overwrite(self):
        self.save_vehicle()
        self.window.part_number.setText('PAD-01')
        self.window.part_source.setText('Parts manual')
        self.window.add_part()
        self.window.linked.setCurrentRow(0)
        self.window.edit_link(self.window.linked.item(0))
        self.store.add_part('JTDA123456789', 'PAD-01', source='Newer source')
        self.window.part_source.setText('Stale source')
        self.window.add_part()
        self.assertEqual(self.store.parts_for('JTDA123456789')[0]['source'], 'Newer source')
        with patch('vehicle_fitment_window.QMessageBox.question', return_value=QMessageBox.Yes):
            self.window.remove_link()
        self.assertEqual(self.store.parts_for('JTDA123456789')[0]['source'], 'Newer source')
        self.assertIn('changed', self.window.status.text().lower())


if __name__ == '__main__':
    unittest.main()
