"""Order lifecycle, checklist persistence, varied speech, and notebook transfers."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QObject, Signal
from notes import NoteStore, NoteService, business_summary, current_guidance, fallback_reminder, note_fingerprint, note_search_text
from smart_notes import SmartNoteAssistant, note_prompt
from business_voice import BusinessVoice
from settings import AppSettings

class Credentials:
    def get(self): return 'placeholder-session'

class Client(QObject):
    completed = Signal(object)
    failed = Signal(str)
    def __init__(self): super().__init__(); self.calls = []
    def send(self, model, messages, tools=True, json_mode=False): self.calls.append((model, messages, tools, json_mode)); return True
    def cancel(self): pass

class BusinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.store = NoteStore(self.root / 'notes.json'); self.settings = AppSettings(self.root / 'settings.json')
        self.details = {'kind': 'order', 'customer': 'Customer A', 'order_ref': 'ORD-001', 'contact': 'Call at pickup',
            'order_status': 'preparing', 'order_due': 500,
            'checklist': [{'id': 'one', 'text': 'Brake pads', 'quantity': 2, 'done': False},
                          {'id': 'two', 'text': 'Air filter', 'quantity': 1, 'done': True}]}
    def tearDown(self): self.temp.cleanup()
    def order(self): return self.store.add('Customer order', 'Pickup details', repeat=30, now=100, details=self.details)
    def test_orders_and_checklists_persist_without_affecting_original_notes(self):
        note = self.order(); self.store.add('Plain note', 'Keep this', now=101)
        restored = NoteStore(self.store.path).find(note['id'])
        self.assertEqual(restored['customer'], 'Customer A'); self.assertTrue(restored['checklist'][1]['done'])
        self.assertEqual(restored['checklist'][0]['quantity'], 2)
        self.assertEqual(NoteStore(self.store.path).notes[1]['kind'], 'note')
    def test_ready_is_open_and_terminal_statuses_stop_reminders(self):
        n = self.order(); self.store.modify(n['id'], order_status='ready')
        self.assertFalse(n['done']); self.assertEqual(business_summary(self.store.notes, 600)['ready_orders'], 1)
        self.store.modify(n['id'], order_status='cancelled'); self.assertTrue(n['done'])
        self.assertIsNone(self.store.next_notification(10000)[0])
        self.store.complete(n['id'], False); self.assertFalse(n['done']); self.assertEqual(n['order_status'], 'new')
    def test_checking_items_changes_ai_context_without_moving_pickup(self):
        n = self.order(); before = note_fingerprint(n)
        items = copy.deepcopy(n['checklist']); items[0]['done'] = True
        self.store.modify(n['id'], checklist=items)
        self.assertNotEqual(before, note_fingerprint(n)); self.assertEqual(n['order_due'], 500)
        self.assertEqual(n['written'], 100, 'Checking items moved the original relative date')
    def test_summary_distinguishes_ready_late_and_unchecked_work(self):
        self.order(); values = business_summary(self.store.notes, 600)
        self.assertEqual((values['open_orders'], values['late_orders'], values['unchecked_items']), (1, 1, 1))
    def test_search_includes_customer_number_and_items(self):
        text = note_search_text(self.order())
        for value in ('Customer A', 'ORD-001', 'Brake pads'): self.assertIn(value, text)
    def test_local_reminders_avoid_recent_phrases_and_completed_items(self):
        n = self.order(); first = fallback_reminder(n); n['reminder_history'] = [first]
        for _ in range(8):
            next_text = fallback_reminder(n); self.assertNotEqual(first, next_text); self.assertNotIn('Air filter', next_text)
    def test_quantity_bounds_and_duplicate_ids_are_validated(self):
        raw = dict(self.order()); raw['checklist'] = [{'id': 'same', 'text': 'A', 'quantity': 999999}, {'id': 'same', 'text': 'B'}]
        n = self.store.validate_note(raw); self.assertEqual(len(n['checklist']), 1); self.assertEqual(n['checklist'][0]['quantity'], 9999)
    def test_backup_merge_preserves_local_pins_and_keeps_newest(self):
        n = self.order(); self.store.modify(n['id'], pinned=True, pin_position=[30, 40])
        incoming = dict(n, updated=n['updated'] + 10, customer='Updated', pinned=False, source='/some/phone/file')
        self.assertEqual(self.store.import_backup({'notes': [incoming]}), 1)
        self.assertEqual(n['customer'], 'Updated'); self.assertTrue(n['pinned']); self.assertEqual(n['pin_position'], [30, 40])
        self.assertEqual(self.store.import_backup({'notes': [dict(incoming, updated=100, customer='Old')]}), 0)
    def test_invalid_backup_is_atomic(self):
        n = self.order()
        with self.assertRaises(ValueError): self.store.import_backup({'notes': [dict(n, id='new'), {'id': 'bad'}]})
        self.assertEqual(len(self.store.notes), 1)
    def test_groq_prompt_uses_live_status_and_recent_reminders(self):
        n = self.order(); n['reminder_history'] = ['A previous phrase']
        payload = json.loads(note_prompt(n, now=600, occasion='reminder')[1]['content'])
        self.assertEqual(payload['occasion'], 'reminder'); self.assertEqual(payload['customer'], 'Customer A')
        self.assertEqual(payload['checklist'][1]['done'], True); self.assertEqual(payload['recent_reminders'], ['A previous phrase'])
    def test_due_reminder_requests_fresh_groq_text_and_repeats_are_filtered(self):
        n = self.order(); n['reminder_history'] = ['A previous phrase']
        service = NoteService(self.store, self.settings); client = Client()
        helper = SmartNoteAssistant(service, self.settings, Credentials(), client=client)
        try:
            helper.request_reminder(n, 'reminder')
            self.assertEqual(len(client.calls), 1)
            client.completed.emit({'content': json.dumps({'reminder': 'A previous phrase', 'next_step': 'Check the remaining pads', 'suggested_due': None, 'reason': ''})})
            self.assertNotEqual(current_guidance(n)['reminder'], 'A previous phrase')
        finally: helper.stop(); service.stop()
    def test_groq_greetings_use_business_context_and_recent_phrases(self):
        client = Client(); spoken = []
        voice = BusinessVoice(self.settings, Credentials(), lambda *_: {'business_mode': True, 'open_orders': 1}, lambda: self.store.notes, spoken.append, lambda: True, client=client)
        try:
            voice.request('startup'); self.assertEqual(len(client.calls), 1)
            client.completed.emit({'content': 'Good morning. Let’s check your first order.'})
            self.assertEqual(spoken[-1], 'Good morning. Let’s check your first order.')
            voice.next_due = 0; voice.request('mouse_greeting')
            self.assertIn(spoken[-1], client.calls[-1][1][0]['content'])
            client.completed.emit({'content': spoken[-1]})
            self.assertNotEqual(spoken[-1], spoken[-2])
        finally: voice.stop()

if __name__ == '__main__': unittest.main()
