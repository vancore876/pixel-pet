"""Desktop business flow with scripted Groq greetings and contextual reminders."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QObject, Signal, QTimer, Qt, QDateTime, qInstallMessageHandler
from PySide6.QtWidgets import QApplication
from main import BuddyApp
from notes import current_guidance, business_summary
from settings import AppSettings

qInstallMessageHandler(lambda kind, context, text: print(text, file=sys.stderr) if 'This plugin does not support' not in text else None)
app = QApplication([]); app.setQuitOnLastWindowClosed(False)
temp = tempfile.TemporaryDirectory(); root = Path(temp.name)
preview = Path(os.environ.get('BUDDY_QA_DIR', root)); preview.mkdir(parents=True, exist_ok=True)
buddy = BuddyApp(app, AppSettings(root / 'settings.json'), show_tray=False)
# This scripted AI check isolates the local business editor; shared transport has its own integration checks.
buddy.notepad.shared_business = None
state, errors = {}, []

class Credentials:
    def get(self): return 'scripted-test-session'
class Client(QObject):
    completed = Signal(object)
    failed = Signal(str)
    def __init__(self, voice=False): super().__init__(); self.calls = []; self.generation = 0; self.voice = voice
    def send(self, model, messages, tools=True, json_mode=False):
        self.calls.append({'messages': messages, 'tools': tools}); generation = self.generation
        text = 'Welcome back. Let’s check the remaining order items.' if self.voice else json.dumps({
            'reminder': ('Check the remaining brake pads for ORD-001.' if len(self.calls) == 1 else 'A fresh order check: prepare the unchecked pads for Customer A.'),
            'next_step': 'Verify the two brake pad sets before handover.', 'suggested_due': None, 'reason': ''})
        QTimer.singleShot(80, lambda: self.completed.emit({'content': text}) if self.generation == generation else None)
        return True
    def cancel(self): self.generation += 1

def replace_client(helper, client):
    helper.client.cancel(); helper.client.completed.disconnect(helper.received); helper.client.failed.disconnect(helper.failed)
    helper.client = client; helper.credentials = Credentials()
    client.completed.connect(helper.received); client.failed.connect(helper.failed)

smart, voice = Client(), Client(True)
replace_client(buddy.smart_notes, smart); replace_client(buddy.voice, voice)
buddy.voice.available = lambda: True

def begin():
    try:
        buddy.apply_settings({'ai_autonomy': False, 'desktop_enabled': False, 'mouse_mode': 'off'})
        window = buddy.notepad; buddy.show_notepad(); window.new_entry('order')
        window.title.setText('Customer pickup'); window.customer.setText('Customer A'); window.contact.setText('Pickup desk')
        window.order_ref.setText('ORD-001'); window.order_status.setCurrentIndex(window.order_status.findData('preparing'))
        window.body.setPlainText('Customer collects the order at the counter.')
        window.order_timed.setChecked(True); window.order_due.setDateTime(QDateTime.currentDateTime().addSecs(3600))
        for text, quantity in (('Brake pads', 2), ('Air filter', 1)):
            window.item_text.setText(text); window.item_quantity.setValue(quantity); window.add_checklist_item()
        window.checklist.item(1, 0).setCheckState(Qt.Checked)
        window.quick_time(900); assert window.save_note(); state['id'] = window.editing_id
        n = buddy.notes_store.find(state['id']); assert n['kind'] == 'order' and n['checklist'][1]['done']
        assert n['checklist'][0]['quantity'] == 2 and n['order_due'] > n['next_due']
        window.load_note(n['id']); assert window.body.toPlainText() == n['body'], 'Loading duplicated checklist details into the body'
        window.show_section('orders'); window.grab().save(str(preview / 'business-orders.png'))
        buddy.note_service.tick(); assert '2 × Brake pads' in buddy.note_popup.body.toPlainText()
        buddy.smart_notes.refresh(); QTimer.singleShot(400, guidance_ready)
    except Exception as exc: errors.append(repr(exc)); app.quit()

def guidance_ready():
    try:
        n = buddy.notes_store.find(state['id']); assert current_guidance(n)
        prompt = json.loads(smart.calls[0]['messages'][1]['content'])
        assert prompt['customer'] == 'Customer A' and prompt['checklist'][1]['done']
        state['first'] = current_guidance(n)['reminder']; buddy.note_popup.grab().save(str(preview / 'order-reminder.png'))
        buddy.smart_notes.next_request = 0; buddy.deliver_note(n, 'reminder')
        buddy.voice.request('startup'); QTimer.singleShot(400, varied_ready)
    except Exception as exc: errors.append(repr(exc)); app.quit()

def varied_ready():
    try:
        n = buddy.notes_store.find(state['id'])
        assert current_guidance(n)['reminder'] != state['first'], 'A due reminder repeated the cached wording'
        assert len(smart.calls) >= 2
        buddy.voice.next_due = 0; state['voice_first'] = buddy.voice.recent[-1]; buddy.voice.request('mouse_greeting')
        QTimer.singleShot(250, finish)
    except Exception as exc: errors.append(repr(exc)); app.quit()

def finish():
    try:
        assert buddy.voice.recent[-1] != state['voice_first'], 'Repeated Groq greeting was not varied'
        context = buddy.ai_context(False, True); n = buddy.notes_store.find(state['id'])
        assert context['business_summary']['open_orders'] == 1
        assert context['incomplete_notes'][0]['customer'] == 'Customer A'
        (preview / 'desktop-order-backup.json').write_text(json.dumps({'version': 2, 'notes': [copy.deepcopy(n)]}))
        window = buddy.notepad; window.order_status.setCurrentIndex(window.order_status.findData('delivered')); assert window.save_note()
        assert n['done'] and business_summary(buddy.notes_store.notes)['open_orders'] == 0
        assert buddy.notes_store.next_notification(time.time() + 10000)[0] is None
        window.new_entry('list'); window.title.setText('Daily preparation'); window.item_text.setText('Review pickups'); window.add_checklist_item(); assert window.save_note()
        assert buddy.notes_store.find(window.editing_id)['kind'] == 'list'
        window.filter.setCurrentIndex(4); assert window.list.count() == 1
        window.grab().save(str(preview / 'business-checklist.png'))
        buddy.shutdown(); assert not buddy.voice.timer.isActive(); app.quit()
    except Exception as exc: errors.append(repr(exc)); app.quit()

QTimer.singleShot(200, begin); QTimer.singleShot(10000, lambda: (errors.append('Business GUI check timed out'), app.quit()))
app.exec(); buddy.shutdown(); temp.cleanup()
if errors: raise AssertionError('; '.join(errors))
print('PASS: order/customer/quantity/checklist/deadline flow, clean reload, grounded Groq reminder, fresh due wording, varied greetings, business context, Delivered stopping reminders, lists, backup, shutdown. Groq was scripted.')
