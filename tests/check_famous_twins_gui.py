"""Isolated Famous Twins Qt workflows; no production files or live accounts."""
import gc
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt, QDateTime
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtGui import QFontDatabase
from auto_parts import CHECKLIST_TEMPLATES, dashboard_summary, order_totals
from notes import NoteStore, NoteService
from notepad_window import NotepadWindow
from settings import AppSettings

app = QApplication.instance() or QApplication([])
app.setQuitOnLastWindowClosed(False)
if app.platformName() == 'offscreen' and Path('C:/Windows/Fonts/segoeui.ttf').is_file():
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeuib.ttf')
temporary = tempfile.TemporaryDirectory(prefix='famous-twins-gui-')
root = Path(temporary.name)
preview = Path(os.environ.get('BUDDY_QA_DIR', root))
preview.mkdir(parents=True, exist_ok=True)
store = NoteStore(root / 'notes.json')
settings = AppSettings(root / 'settings.json')
service = NoteService(store, settings)
window = NotepadWindow(service, settings)
events = []
window.business_event.connect(events.append)

try:
    assert list(window.section_indices) == ['overview', 'writing', 'checklists', 'orders', 'schedule', 'advice', 'import']
    window.resize(1100, 760)
    window.show()
    app.processEvents()
    assert window.new_note()
    assert window.editor_tabs.currentIndex() == window.section_indices['writing']
    window.title.setText('Counter notes')
    window.body.setPlainText('Confirm engine variant before looking up the part.')
    assert window.save_note()
    writing_id = window.editing_id
    window.body.setPlainText('Keep this unsaved counter note.')
    window.show_section('orders')
    app.processEvents()
    assert window.entry_mismatch.isVisible() and not window.editor_tabs.isVisible()
    assert window.list.count() == 0 and window.dirty
    assert window.body.toPlainText() == 'Keep this unsaved counter note.'
    window.return_to_entry.click()
    assert window.current_section() == 'writing' and window.editor_tabs.isVisible()
    assert window.edit_state.text() == 'Unsaved changes'
    with patch('notepad_window.QMessageBox.question', return_value=QMessageBox.Cancel):
        assert not window.new_entry('order')
    assert window.body.toPlainText() == 'Keep this unsaved counter note.'
    assert window.save_note()

    assert window.new_entry('list')
    window.checklist_template.setCurrentText('Opening')
    window.apply_checklist_template()
    assert window.task_table.rowCount() == len(CHECKLIST_TEMPLATES['Opening'])
    window.apply_checklist_template()
    assert window.task_table.rowCount() == len(CHECKLIST_TEMPLATES['Opening']), 'Template duplicated tasks'
    window.task_table.item(0, 0).setCheckState(Qt.Checked)
    assert window.checklist_progress.value() == 1
    window.checklist_notes.setPlainText('Use the opening routine at the parts counter.')
    assert window.body.toPlainText() == window.checklist_notes.toPlainText()
    assert window.save_note()
    checklist_id = window.editing_id
    assert store.find(checklist_id)['kind'] == 'list'
    assert window.list.count() == 1 and window.list.item(0).data(Qt.UserRole) == checklist_id
    assert window.create_button.text() == '+ New checklist'
    assert events[-1] == 'checklist_saved'
    app.processEvents()
    assert window.grab().save(str(preview / 'famous-twins-checklists.png'))

    assert window.new_entry('order')
    window.title.setText('Brake service parts')
    window.customer.setText('Marcia Brown')
    window.contact.setText('876-555-0100')
    window.order_ref.setText('FT-1001')
    window.vehicle.setText('2014 Toyota Corolla 1.8')
    window.registration.setText('1234 AB')
    window.vin.setText('MANUAL-CHASSIS-ENTRY')
    window.priority.setCurrentIndex(window.priority.findData('urgent'))
    window.order_status.setCurrentIndex(window.order_status.findData('preparing'))
    window.part_text.setText('Front brake pads')
    window.part_number.setText('BP-42')
    window.part_supplier.setText('Local Parts Supplier')
    window.part_bin.setText('A-03')
    window.part_quantity.setValue(2)
    window.part_price.setText('4250.25')
    window.part_stock.setCurrentIndex(window.part_stock.findData('to_order'))
    window.add_order_item()
    window.part_text.setText('Oil filter')
    window.part_price.setText('1800.00')
    window.part_stock.setCurrentIndex(window.part_stock.findData('in_stock'))
    window.add_order_item()
    window.payment_received.setText('5000.00')
    window.order_notes.setPlainText('Confirm fitment with the customer before pickup.')
    window.order_timed.setChecked(True)
    window.order_due.setDateTime(QDateTime.currentDateTime().addSecs(7200))
    window.remind_at_order_deadline()
    assert window.editor_tabs.currentIndex() == window.section_indices['schedule']
    assert window.scheduled.isChecked()
    window.schedule_follow_up()
    assert 3590 < window.due.dateTime().toSecsSinceEpoch() - QDateTime.currentSecsSinceEpoch() <= 3600
    assert window.save_note()
    order_id = window.editing_id
    order = store.find(order_id)
    assert window.list.count() == 3, 'Schedule should browse all record types'
    assert order_totals(order) == {'subtotal_cents': 1030050, 'paid_cents': 500000, 'balance_cents': 530050, 'credit_cents': 0}
    assert events[-1] == 'order_saved'
    assert order['vehicle'] == '2014 Toyota Corolla 1.8'
    assert order['checklist'][0]['part_number'] == 'BP-42'
    window.load_note(order_id)
    assert window.order_notes.toPlainText() == order['body'] == window.body.toPlainText()
    assert window.order_table.item(0, 6).text() == '4250.25'
    assert window.copy_pickup_summary()
    assert 'Balance: JMD 5,300.50' in app.clipboard().text()
    assert 'BP-42' in app.clipboard().text()
    app.processEvents()
    assert window.grab().save(str(preview / 'famous-twins-orders.png'))

    window.show_section('overview')
    window.refresh()
    summary = dashboard_summary(store.notes)
    assert summary['open_orders'] == 1 and summary['urgent_orders'] == 1
    assert window.sourcing_table.rowCount() == 1
    assert window.sourcing_table.item(0, 1).text() == '2'
    window.history_search.setText('1234 AB')
    assert window.history_table.rowCount() == 1
    window.open_history_order(0, 0)
    assert window.editing_id == order_id and window.editor_tabs.currentIndex() == window.section_indices['orders']
    window.order_status.setCurrentIndex(window.order_status.findData('ready'))
    assert window.save_note() and events[-1] == 'order_ready'
    window.payment_received.setText('11000.00')
    assert 'Credit JMD 699.50' in window.order_total_label.text()
    assert window.save_note()
    window.show_section('overview')
    app.processEvents()
    assert window.grab().save(str(preview / 'famous-twins-overview.png'))
    window.resize(800, 600)
    window.show_section('orders')
    app.processEvents()
    assert window.width() == 800 and window.height() == 600
    assert window.sections.visualItemRect(window.sections.item(window.sections.count() - 1)).bottom() <= window.sections.viewport().height(), 'Navigation is clipped on a small display'
    assert not window.browser_panel.isVisible() and window.browser_toggle.isVisible()
    window.browser_toggle.click()
    assert window.browser_panel.isVisible()
    window.browser_toggle.click()
    assert not window.browser_panel.isVisible()
    assert window.save_button.isVisible() and window.save_button.geometry().height() > 0
    assert window.more_actions.isVisible() and not window.remove.isVisible()
    assert window.grab().save(str(preview / 'famous-twins-orders-800.png'))
    settings.values['interface_appearance'] = 'dark'
    window.configure()
    app.processEvents()
    assert window.grab().save(str(preview / 'famous-twins-orders-dark-800.png'))
    settings.values['interface_appearance'] = 'light'
    window.configure()

    # A failed shared save must preserve the complete draft and never become a local save.
    class Shared:
        def __init__(self): self.callback = None; self.marked = []
        def mark_editing(self, identifier): self.marked.append(identifier)
        def save(self, identifier, title, body, repeat, due, details, callback):
            self.callback = callback
            return True
    shared = Shared()
    window.shared_business = shared
    window.load_note(order_id)
    window.order_notes.setPlainText('Unsaved coworker draft survives a server conflict.')
    assert window.save_note() and window.business_pending and window.dirty
    assert not window.editor_tabs.isEnabled()
    shared.callback(None, 'Another coworker changed this entry. Reload saved before retrying.')
    assert not window.business_pending and window.dirty and window.editor_tabs.isEnabled()
    assert window.reload_button.isVisible(), 'Conflict recovery must be visible without opening a menu'
    assert window.body.toPlainText().startswith('Unsaved coworker draft')
    window.open_workspace_note(order_id, 'schedule')
    assert window.dirty and window.body.toPlainText().startswith('Unsaved coworker draft'), 'Opening the current entry discarded its draft'
    assert store.find(order_id)['body'].startswith('Confirm fitment')
    with patch('notepad_window.QMessageBox.question', return_value=QMessageBox.Discard):
        window.reload_saved()
    assert not window.dirty and window.body.toPlainText().startswith('Confirm fitment')
    assert not window.reload_button.isVisible()
    # Clean remote changes refresh every editor field and capture the new revision.
    window.show_section('schedule')
    marked = len(shared.marked)
    store.find(order_id).update(title='Updated by coworker', body='New saved supplier note.')
    window.refresh()
    assert window.title.text() == 'Updated by coworker' and window.body.toPlainText() == 'New saved supplier note.'
    assert window.editor_tabs.currentIndex() == window.section_indices['schedule']
    assert len(shared.marked) > marked
    window.order_notes.setPlainText('Private unsaved draft.')
    marked = len(shared.marked)
    store.find(order_id).update(title='Another remote edit', body='Remote saved body.')
    window.refresh()
    assert window.title.text() == 'Updated by coworker' and window.body.toPlainText() == 'Private unsaved draft.'
    assert len(shared.marked) == marked, 'Incoming changes advanced the dirty draft revision'
    with patch('notepad_window.QMessageBox.question', return_value=QMessageBox.Discard):
        window.reload_saved()
    assert window.title.text() == 'Another remote edit' and window.body.toPlainText() == 'Remote saved body.'
    window.kind.setCurrentIndex(window.kind.findData('note'))
    with patch('notepad_window.QMessageBox.warning') as warning:
        assert not window.save_note() and warning.called
    window.kind.setCurrentIndex(window.kind.findData('order'))
    window.dirty = False
    class Rejected(Shared):
        def save(self, identifier, title, body, repeat, due, details, callback):
            callback(None, 'Specific server validation error')
            return False
    window.shared_business = Rejected()
    window.order_notes.setPlainText('Retry draft.')
    assert not window.save_note()
    assert 'Specific server validation error' in window.status.text() and window.dirty
    store.state['notes'] = [note for note in store.notes if note['id'] != order_id]
    with patch('notepad_window.QMessageBox.question', return_value=QMessageBox.Discard):
        window.reload_saved()
    assert window.editing_id is None and not window.dirty
    window.shared_business = None

    # Writing remains separate, and large documents still open as paged read-only text.
    assert window.new_note()
    window.title.setText('Supplier reference')
    window.body.setPlainText('First page supplier data.\n' + 'part catalogue reference ' * 1100)
    assert window.save_note()
    document = store.find(window.editing_id)
    assert document.get('document_id') and window.body.isReadOnly()
    assert window.document_page.maximum() > 1
    first = window.body.toPlainText()
    window.document_page.setValue(2)
    assert window.body.toPlainText() != first
    service.modify(document['id'], pinned=True)
    assert window.document_page.value() == 2, 'An unrelated update reset the document reading page'
    assert store.find(writing_id)['kind'] == 'note' and store.find(checklist_id)['kind'] == 'list'
    print('PASS: separate workspace pages, preserved drafts, checklist templates/progress, rich auto-parts order, exact JMD amounts/credit, pickup clipboard, follow-up schedule, dashboard/sourcing/customer history, shared-save failure recovery, 800×600 layout, and full-document paging.')
    print('Screenshots:', preview)
finally:
    window.dirty = False
    window.shutdown()
    service.stop()
    window.close()
    app.processEvents()
    gc.collect()
    temporary.cleanup()
