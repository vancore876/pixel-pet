"""Home navigation works with saved records without silently editing them."""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt, QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from settings import AppSettings
from notes import NoteService, NoteStore
from focus import FocusSession
from home_window import HomeWindow
from ui_style import ui_palette

APP = QApplication.instance() or QApplication([])


class HomeInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = AppSettings(root / 'settings.json')
        self.store = NoteStore(root / 'notes.json')
        self.service = NoteService(self.store, self.settings)
        self.focus = FocusSession()
        self.window = HomeWindow(self.service, self.settings, self.focus)

    def tearDown(self):
        self.window.shutdown()
        self.window.deleteLater()
        self.focus.reset()
        self.service.stop()
        self.store.close()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.temp.cleanup()

    def test_search_uses_real_records_and_opens_without_changing_them(self):
        private = self.service.save_note(None, 'Private text', 'Keep my writing local', 0)
        order = self.service.save_note(None, 'Brake pads', 'Confirm fitment', 0, details={
            'kind': 'order', 'customer': 'Marcia', 'vehicle': 'Toyota Corolla',
            'priority': 'urgent', 'order_status': 'ready', 'order_ref': 'FT-42'})
        self.assertEqual(self.window.attention.item(0).data(Qt.UserRole), order['id'])
        self.window.search.setText('Corolla')
        opened = []
        self.window.record_requested.connect(opened.append)
        self.window.open_first_result()
        self.assertEqual(opened, [order['id']])
        self.assertEqual(self.window.recent.count(), 1)
        self.window.search.setText('no such customer')
        self.window.open_first_result()
        self.assertEqual(opened, [order['id']])
        self.assertIsNone(self.window.recent.item(0).data(Qt.UserRole))
        self.assertEqual(self.store.find(private['id'])['body'], 'Keep my writing local')

    def test_primary_actions_emit_navigation_without_creating_dummy_orders(self):
        created, navigated = [], []
        self.window.create_requested.connect(created.append)
        self.window.navigate_requested.connect(navigated.append)
        self.window.create_buttons['order'].click()
        self.window.nav_buttons['coworkers'].click()
        self.assertEqual(created, ['order'])
        self.assertEqual(navigated, ['coworkers'])
        self.assertFalse(self.store.notes)

    def test_readable_counts_and_disconnected_state_do_not_claim_live_inventory(self):
        self.service.save_note(None, 'Customer order', 'Check manual stock', 0, details={
            'kind': 'order', 'payment_received_cents': 500000, 'checklist': [
                {'id': 'pads', 'text': 'Pads', 'quantity': 2, 'done': False, 'unit_price_cents': 425025}]})
        self.assertEqual(self.window.metric_labels['open_orders'].text(), '1')
        self.assertEqual(self.window.metric_labels['balance_cents'].text(), 'JMD 3,500.50')
        self.window.set_connection('Sign in to Work Chat', False)
        self.assertEqual(self.window.connection_badge.text(), 'Office not connected')
        self.window.set_connection('Shared with coworkers · alice', True)
        self.assertEqual(self.window.connect_button.text(), 'Open team chat')

    def test_light_dark_and_compact_layout_preserve_actions(self):
        self.window.show()
        for appearance in ('light', 'dark', 'system'):
            self.settings.values['interface_appearance'] = appearance
            self.window.configure()
            self.window.resize(800, 600)
            APP.processEvents()
            self.assertEqual(self.window.width(), 800)
            self.assertEqual(self.window.scroll.horizontalScrollBar().maximum(), 0)
            self.assertTrue(self.window.create_buttons['order'].isVisible())
            self.assertTrue(self.window.nav_buttons['coworkers'].isVisible())
        self.assertNotEqual(ui_palette({'interface_appearance': 'light'})['bg'],
                            ui_palette({'interface_appearance': 'dark'})['bg'])

    def test_focus_and_quiet_controls_have_explicit_effects(self):
        changes = []
        self.window.preference_requested.connect(lambda k, v: changes.append((k, v)))
        self.window.quiet.setChecked(True)
        self.assertEqual(changes, [('quiet_mode', True)])
        self.assertFalse(self.settings['quiet_mode'], 'Preference signals must not mutate settings without the coordinator')
        self.window.focus_start.click()
        self.assertEqual(self.focus.clock.state, 'running')
        self.window.focus_pause.click()
        self.assertEqual(self.focus.clock.state, 'paused')
        self.window.focus_pause.click()
        self.assertEqual(self.focus.clock.state, 'running')
        self.window.focus_reset.click()
        self.assertEqual(self.focus.clock.state, 'idle')


if __name__ == '__main__':
    unittest.main()
