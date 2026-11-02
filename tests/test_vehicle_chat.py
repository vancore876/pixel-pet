"""Offline chat must distinguish saved chassis links from catalogue guesses."""
import csv
from pathlib import Path
import tempfile
import unittest

from PySide6.QtWidgets import QApplication
from ai_chat import ChatWindow
from parts_catalog import FIELDS
from settings import AppSettings
from test_parts_chat import Client, Credentials

APP = QApplication.instance() or QApplication([])


class VehicleChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.client = Client()
        self.chat = ChatWindow(AppSettings(root / 'settings.json'), Credentials(),
                               lambda *args: {}, lambda action: 'ok', client=self.client)
        source = root / 'catalogue-2026-10-22.csv'
        with source.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(FIELDS))
            writer.writeheader()
            writer.writerow({**{key: '' for key in FIELDS}, 'PartNumber': 'BP-44',
                             'Description': 'DISC PAD FRT TOY COROLLA', 'StoreBin1': 'A-12'})
        self.chat.parts_catalog.import_csv(source)
        self.chat.fitment_store.save_vehicle('FAKE12345678', 'Toyota', 'Corolla', year=2015, engine='1NZ')
        self.chat.fitment_store.add_part('FAKE12345678', 'BP-44', source='Parts manual', note='Front')

    def tearDown(self):
        self.chat.shutdown()
        self.chat.close()
        self.temp.cleanup()

    def test_saved_links_and_unverified_candidates_are_separate_offline(self):
        responses = []
        self.chat.reply_ready.connect(responses.append)
        self.chat.input.setText('What brake pads fit chassis FAKE12345678?')
        self.chat.submit()
        reply = responses[-1]
        self.assertIn('Toyota Corolla', reply)
        self.assertIn('Staff-entered part links', reply)
        self.assertIn('BP-44', reply)
        self.assertIn('Parts manual', reply)
        self.assertIn('unverified', reply)
        self.assertIn('2026-10-22', reply)
        self.assertIn('Confirm part number, vehicle year, engine and fitment', reply)
        self.assertEqual(self.client.calls, [])

    def test_unknown_chassis_is_not_guessed(self):
        reply = self.chat.local_vehicle_reply('What parts fit chassis UNKNOWN99999?')
        self.assertIn('No vehicle mapping is saved', reply)
        self.assertNotIn('Toyota', reply)
        self.assertNotIn('BP-44', reply)

    def test_changed_vehicle_marks_existing_link_for_reconfirmation(self):
        self.chat.fitment_store.save_vehicle('FAKE12345678', 'Honda', 'Civic', year=2018)
        reply = self.chat.local_vehicle_reply('What fits chassis FAKE12345678?')
        self.assertIn('needs re-confirmation', reply)
        self.assertNotIn('current vehicle details', reply)

    def test_description_matches_do_not_assert_year_or_fitment(self):
        reply = self.chat.local_vehicle_reply('What brake pads fit a 2015 Toyota Corolla?')
        self.assertIn('BP-44', reply)
        self.assertIn('unverified', reply)
        self.assertIn('Confirm part number, vehicle year, engine and fitment', reply)
        self.assertNotIn('Saved vehicle', reply)

    def test_general_and_ordinary_part_questions_do_not_become_vehicle_queries(self):
        self.assertIsNone(self.chat.local_vehicle_reply('Tell me a joke about Toyota Corolla parts'))
        self.assertIsNone(self.chat.local_vehicle_reply('Where is part BP-44?'))
        self.assertIn('BP-44', self.chat.local_parts_reply('Where is part BP-44?'))

    def test_bare_number_matching_a_chassis_and_part_requires_clarification(self):
        source = Path(self.temp.name) / 'collision.csv'
        with source.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(FIELDS))
            writer.writeheader()
            writer.writerow({**{key: '' for key in FIELDS}, 'PartNumber': 'FAKE12345678',
                             'Description': 'A part with the same code as the chassis'})
        self.chat.parts_catalog.import_csv(source)
        reply = self.chat.local_vehicle_reply('FAKE12345678')
        self.assertIn('both a part and a chassis', reply)
        self.assertIn('Please say', reply)


if __name__ == '__main__':
    unittest.main()
