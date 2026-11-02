"""Real chat routing for an imported local parts snapshot."""
import csv
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from ai_chat import ChatWindow
from parts_catalog import FIELDS
from settings import AppSettings

APP = QApplication.instance() or QApplication([])


class Client(QObject):
    completed = Signal(object)
    failed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.calls = []
        self.reply = None

    def send(self, model, messages, **kwargs):
        self.calls.append(messages)
        return True

    def cancel(self):
        pass


class Credentials:
    def get(self):
        return ''


class PartsChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.client = Client()
        self.chat = ChatWindow(AppSettings(root / 'settings.json'), Credentials(),
                               lambda *args: {}, lambda action: 'ok', client=self.client)
        self.csv_path = root / 'parts-2026-10-22.csv'
        with self.csv_path.open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(FIELDS))
            writer.writeheader()
            writer.writerow({**{key: '' for key in FIELDS}, 'PartNumber': 'BP-44',
                             'Description': 'BRAKE PAD', 'StoreBin1': 'A-12',
                             'StoreQuantity': '-1.5', 'RetailPrice': '100', 'CostPrice': 'SECRET-COST'})
            writer.writerow({**{key: '' for key in FIELDS}, 'PartNumber': '-11044',
                             'Description': 'DISC PAD FRT TOY COROLLA 2012', 'StoreBin1': 'B-33'})
            writer.writerow({**{key: '' for key in FIELDS}, 'PartNumber': '11044',
                             'Description': 'OTHER ITEM', 'StoreBin1': 'C-44'})
        self.chat.parts_catalog.import_csv(self.csv_path)

    def tearDown(self):
        self.chat.shutdown()
        self.chat.close()
        self.temp.cleanup()

    def test_parts_questions_are_local_and_snapshot_labelled(self):
        for question in ('Where is part BP-44?',
                         'How much is BP-44?', 'BP-44'):
            with self.subTest(question=question):
                answers = []
                self.chat.reply_ready.connect(answers.append)
                self.chat.input.setText(question)
                self.chat.submit()
                self.chat.reply_ready.disconnect(answers.append)
                self.assertEqual(len(answers), 1)
                self.assertIn('BP-44', answers[0])
                self.assertIn('2026-10-22', answers[0])
                self.assertIn('A-12', answers[0])
                self.assertIn('-1.5', answers[0])
                self.assertNotIn('SECRET-COST', answers[0])
                self.assertEqual(self.client.calls, [])
                self.assertEqual(self.chat.history, [])

    def test_not_found_and_general_chat_are_distinguished(self):
        self.assertIn('could not find', self.chat.local_parts_reply('Find part ZZ-999'))
        self.assertIsNone(self.chat.local_parts_reply('Where is my notepad?'))
        self.assertIsNone(self.chat.local_parts_reply('Tell me a joke'))
        self.assertIsNone(self.chat.local_parts_reply('Tell me a joke about car parts'))

    def test_cloud_parts_is_an_explicit_choice(self):
        self.chat.parts_use_groq.setChecked(True)
        self.chat.input.setText('Where is part BP-44?')
        self.chat.submit()
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(self.client.calls[0][-1]['content'], 'Where is part BP-44?')

    def test_conversation_and_chassis_are_local_without_a_key(self):
        path = Path(self.temp.name) / 'frames.txt'
        path.write_text('AB12345678\n', encoding='utf-8')
        self.chat.chassis_catalog.import_text(path)
        replies = []
        self.chat.reply_ready.connect(replies.append)
        for message in ('Hello', 'My name is Sam', 'What is my name?',
                        'Is chassis AB12345678 on the list?', 'chassis ZZ99999999',
                        'Tell me something unexpected'):
            self.chat.input.setText(message)
            self.chat.submit()
        self.assertIn('Hello', replies[0])
        self.assertIn('Sam', replies[2])
        self.assertIn('is in the saved chassis list', replies[3])
        self.assertIn('does not identify the vehicle', replies[3])
        self.assertIn('not found', replies[4])
        self.assertIn('do not understand', replies[5])
        self.assertIn('is in the saved chassis list', self.chat.local_chassis_reply('AB12345678'))
        self.assertIsNone(self.chat.local_chassis_reply('BP-44'))
        self.assertEqual(self.client.calls, [])

    def test_part_followup_requires_a_single_previous_result(self):
        self.chat.input.setText('BP-44')
        self.chat.submit()
        self.chat.input.setText('Where is it?')
        self.chat.submit()
        self.assertIn('A-12', self.chat.transcript.toPlainText())
        frames = Path(self.temp.name) / 'frames.txt'
        frames.write_text('AB12345678\n', encoding='utf-8')
        self.chat.chassis_catalog.import_text(frames)
        self.chat.input.setText('chassis AB12345678')
        self.chat.submit()
        self.chat.input.setText('Where is it?')
        self.chat.submit()
        self.assertIn('which part number', self.chat.transcript.toPlainText())
        self.chat.clear_conversation()
        self.chat.input.setText('Where is it?')
        self.chat.submit()
        self.assertIn('which part number', self.chat.transcript.toPlainText())
        self.assertEqual(self.client.calls, [])

    def test_parts_question_without_catalogue_explains_import(self):
        with tempfile.TemporaryDirectory() as root:
            from parts_catalog import PartsCatalog
            self.chat.parts_catalog = PartsCatalog(Path(root) / 'empty.sqlite')
            self.assertIn('Import a parts CSV', self.chat.local_parts_reply('Do you have brake pads?'))

    def test_abbreviated_descriptions_and_leading_minus_remain_distinct(self):
        reply = self.chat.local_parts_reply('Where are front brake pads for Toyota Corolla?')
        self.assertIn('-11044', reply)
        self.assertIn('B-33', reply)
        self.assertIn('Confirm the part number, vehicle fitment', reply)
        self.assertIn('-11044', self.chat.local_parts_reply('Do you have brake pads?'))
        self.assertIn('-11044', self.chat.local_parts_reply('Where can I find brake pad?'))
        for question in ('-11044', 'Where is part -11044?'):
            reply = self.chat.local_parts_reply(question)
            self.assertIn('B-33', reply)
            self.assertNotIn('C-44', reply)

    def test_selected_file_import_runs_off_ui_thread(self):
        with patch('ai_chat.QFileDialog.getOpenFileName', return_value=(str(self.csv_path), '')):
            self.chat.import_parts_list()
        self.assertIsNotNone(self.chat._parts_future)
        self.chat._parts_future.result(timeout=10)
        self.chat._parts_import_finished()
        self.assertIn('Imported 3 parts', self.chat.status.text())
        self.assertTrue(self.chat.parts_import.isEnabled())


if __name__ == '__main__':
    unittest.main()
