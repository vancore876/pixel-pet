"""Visual desktop activities never activate a tab or operate on a file."""
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QObject, QRect, Signal, Qt, QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QWidget
from config import DEFAULTS
from real_desktop import RealDesktopPlay

APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)


def target(identifier, kind, rect, **extra):
    return {'id': identifier, 'kind': kind, 'rect': rect,
            'name': identifier, 'path': 'C:/Desktop/' + identifier,
            'hwnd': 1234, 'pid': 100, 'runtime': '42,7', **extra}


class Bridge(QObject):
    changed = Signal()
    status_changed = Signal(str)

    def __init__(self):
        super().__init__()
        # Deliberately return the containing window first, as native scans can.
        self.targets = [target('window', 'window', [50, 80, 680, 600]),
                        target('folder', 'folder', [200, 300, 110, 100]),
                        target('invoice.xlsx', 'file', [225, 320, 38, 38]),
                        target('tab', 'tab', [420, 150, 180, 34])]
        self.status, self.monitors, self.calls = 'Visible targets', [], []

    def target(self, identifier):
        return next((t for t in self.targets if t['id'] == identifier), None)

    def rect(self, target):
        return QRect(*target['rect'])

    def request(self, action, payload, callback):
        self.calls.append((action, payload))
        callback({'selected': True}, '')

    def set_status(self, text):
        self.status = text

    def configure(self): pass
    def scan(self): pass
    def stop(self): pass


class Pet(QWidget):
    hop_completed = Signal(str)
    drag_started = Signal()
    drag_released = Signal(object)

    def __init__(self):
        super().__init__()
        self.resize(96, 96)
        self.move(50, 500)
        self.hidden_in, self.hop_target = '', ''
        self.hop_motion = self.drop_motion = self.drag_offset = self.action_state = self.queued_action = None
        self.paused = False
        self.actions, self.messages = [], []
        self.real_x, self.real_y = 50.0, 500.0

    def hop_to(self, point, identifier):
        self.hop_motion = SimpleNamespace(end_x=point[0], end_y=point[1], elapsed=0.25)
        self.hop_target = identifier

    def land(self):
        identifier = self.hop_target
        self.move(self.hop_motion.end_x, self.hop_motion.end_y)
        self.cancel_hop()
        self.hop_completed.emit(identifier)

    def perform(self, state, seconds=3):
        self.action_state = state
        self.actions.append(state)

    def hide_in(self, identifier):
        self.hidden_in = identifier
        self.hide()

    def come_out(self, point):
        self.hidden_in = ''
        self.show()

    def cancel_hop(self):
        self.hop_motion, self.hop_target = None, ''

    def begin_drop(self):
        self.drop_motion = True

    def position_bubble(self): pass

    def say(self, text):
        self.messages.append(text)


class DesktopInteractionTests(unittest.TestCase):
    def setUp(self):
        self.settings, self.pet, self.bridge = dict(DEFAULTS), Pet(), Bridge()
        self.desktop = RealDesktopPlay(self.pet, self.settings, bridge=self.bridge)
        self.desktop.random_timer.stop()

    def tearDown(self):
        self.desktop.stop()
        for widget in (self.desktop.panel, self.desktop.text_window, self.desktop.peek_sprite, self.pet):
            widget.hide()
            widget.deleteLater()
        self.desktop.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def choose(self, identifier):
        self.desktop.panel.refresh()
        items = self.desktop.panel.targets
        for index in range(items.count()):
            if items.item(index).data(Qt.UserRole) == identifier:
                items.setCurrentRow(index)
                return
        self.fail('Missing target ' + identifier)

    def test_file_inspection_and_dusting_are_visual_only(self):
        self.desktop.jump_path('c:/desktop/INVOICE.XLSX')
        self.assertEqual(self.desktop.pending_id, 'invoice.xlsx')
        self.pet.land()
        self.assertEqual(self.pet.action_state, 'INSPECT')
        self.desktop.advance_interaction()
        self.assertEqual(self.pet.action_state, 'HEAD_TILT')
        self.desktop.advance_interaction()
        self.assertEqual(self.pet.action_state, 'SNIFF')
        self.desktop.visit('invoice.xlsx', 'dust')
        self.pet.land()
        self.assertEqual(self.pet.action_state, 'DUST')
        self.assertEqual(self.pet.hidden_in, '')
        self.assertEqual(self.bridge.calls, [])

    def test_drop_picks_smallest_icon_over_parent_folder_and_window(self):
        self.desktop.drop_on_target([240, 335])
        self.assertEqual(self.desktop.pending_id, 'invoice.xlsx')
        self.desktop.emerge(silent=True)
        self.desktop.drop_on_target([290, 380])
        self.assertEqual(self.desktop.pending_id, 'folder')
        self.desktop.emerge(silent=True)
        self.desktop.drop_on_target([460, 160])
        self.assertEqual(self.desktop.pending_id, 'tab')
        self.desktop.emerge(silent=True)
        self.desktop.drop_on_target([700, 500])
        self.assertEqual(self.desktop.pending_id, 'window')

    def test_folder_knock_transitions_without_hiding_and_default_still_peeks(self):
        self.desktop.visit('folder', 'knock')
        self.pet.land()
        self.assertEqual(self.pet.action_state, 'KNOCK')
        self.assertFalse(self.pet.hidden_in)
        self.desktop.advance_interaction()
        self.assertEqual(self.pet.action_state, 'LISTEN')
        self.desktop.visit('folder')
        self.pet.land()
        self.assertEqual(self.pet.hidden_in, 'desktop:folder')
        self.assertTrue(self.desktop.peek_timer.isActive())
        self.desktop.peek()
        self.assertTrue(self.desktop.peek_sprite.isVisible())
        self.desktop.emerge(silent=True)
        self.assertFalse(self.pet.hidden_in)
        self.assertFalse(self.desktop.peek_sprite.isVisible())

    def test_perching_and_riding_never_select_a_tab(self):
        for activity, state in ((None, 'BALANCE'), ('perch', 'PERCH'), ('peek', 'PEEK')):
            self.desktop.visit('tab', activity)
            self.pet.land()
            self.assertEqual(self.pet.action_state, state)
            self.assertEqual(self.bridge.calls, [])
        self.desktop.select_tab('tab')
        self.assertEqual(self.bridge.calls, [('select_tab', {
            'hwnd': 1234, 'pid': 100, 'runtime': '42,7', 'name': 'tab'})])

    def test_pending_disappearance_cancels_hop_and_ignores_late_arrival(self):
        self.desktop.visit('invoice.xlsx', 'dust')
        self.bridge.targets = [t for t in self.bridge.targets if t['id'] != 'invoice.xlsx']
        self.bridge.changed.emit()
        self.assertFalse(self.desktop.pending_id or self.desktop.active_id)
        self.assertIsNone(self.pet.hop_motion)
        self.pet.hop_completed.emit('desktop:invoice.xlsx')
        self.assertFalse(self.desktop.return_timer.isActive())
        self.assertFalse(self.desktop.action_timer.isActive())

    def test_moving_target_updates_existing_hop_and_following_position(self):
        self.desktop.visit('invoice.xlsx')
        hop = self.pet.hop_motion
        previous_x = hop.end_x
        self.bridge.target('invoice.xlsx')['rect'][0] += 80
        self.bridge.changed.emit()
        self.assertIs(self.pet.hop_motion, hop)
        self.assertEqual(hop.elapsed, 0.25)
        self.assertEqual(hop.end_x, previous_x + 80)
        self.pet.land()
        previous_x = self.pet.x()
        self.bridge.target('invoice.xlsx')['rect'][0] += 60
        self.bridge.changed.emit()
        self.assertEqual(self.pet.x(), previous_x + 60)
        self.assertEqual(self.pet.real_x, float(self.pet.x()))
        self.bridge.targets = []
        self.bridge.changed.emit()
        self.assertFalse(self.desktop.active_id)
        self.assertIsNone(self.pet.action_state)
        self.assertFalse(self.desktop.action_timer.isActive())

    def test_minimized_owner_cancels_pending_activity(self):
        self.bridge.target('tab')['window_rect'] = [50, 80, 680, 600]
        self.desktop.visit('tab')
        user32 = SimpleNamespace(
            GetWindowThreadProcessId=Mock(side_effect=lambda hwnd, pid: setattr(pid._obj, 'value', 100)),
            IsWindowVisible=Mock(return_value=True), IsIconic=Mock(return_value=True),
            GetWindowRect=Mock())
        with patch('real_desktop.ctypes.windll', SimpleNamespace(user32=user32), create=True), \
                patch('real_desktop.sys.platform', 'win32'):
            self.desktop.follow_window()
        self.assertFalse(self.desktop.pending_id)
        self.assertIsNone(self.pet.hop_motion)
        user32.GetWindowRect.assert_not_called()

    def test_invalid_activity_does_not_interrupt_current_play(self):
        self.desktop.visit('tab', 'perch')
        hop = self.pet.hop_motion
        result = self.desktop.visit('invoice.xlsx', 'hide')
        self.assertIn('Choose an activity', result)
        self.assertEqual(self.desktop.pending_id, 'tab')
        self.assertIs(self.pet.hop_motion, hop)

    def test_folder_removed_before_peek_does_not_leave_a_ghost_sprite(self):
        self.desktop.visit('folder')
        self.pet.land()
        self.bridge.targets = []
        self.desktop.peek()
        self.assertFalse(self.desktop.active_id or self.pet.hidden_in)
        self.assertFalse(self.desktop.peek_sprite.isVisible())
        self.assertFalse(self.desktop.peek_timer.isActive())

    def test_panel_actions_and_tab_selection_follow_current_target(self):
        panel = self.desktop.panel
        self.assertFalse(panel.visit_button.isEnabled())
        self.choose('invoice.xlsx')
        self.assertEqual(panel.interactions.currentData(), 'inspect')
        self.assertEqual(panel.interactions.count(), 2)
        self.assertTrue(panel.visit_button.isEnabled())
        self.assertFalse(panel.select_tab_button.isEnabled())
        panel.interactions.setCurrentIndex(1)
        self.bridge.changed.emit()
        self.assertEqual(panel.interactions.currentData(), 'dust')
        panel.visit()
        self.assertEqual(self.desktop.interaction, 'dust')
        self.choose('tab')
        self.assertTrue(panel.select_tab_button.isEnabled())
        self.assertEqual(panel.interactions.currentData(), 'ride')
        self.bridge.targets = []
        self.bridge.changed.emit()
        self.assertFalse(panel.visit_button.isEnabled())
        self.assertFalse(panel.select_tab_button.isEnabled())

    def test_automatic_play_respects_quiet_low_power_paused_and_busy(self):
        with patch('real_desktop.random.random', return_value=0):
            for key in ('quiet_mode', 'low_power'):
                self.settings[key] = True
                self.desktop.random_visit()
                self.assertFalse(self.desktop.pending_id)
                self.settings[key] = False
            self.pet.paused = True
            self.desktop.random_visit()
            self.assertFalse(self.desktop.pending_id)
            self.pet.paused = False
            self.desktop.activity_blocked = lambda: True
            self.desktop.random_visit()
            self.assertFalse(self.desktop.pending_id)
            self.desktop.activity_blocked = lambda: False
            self.desktop.random_visit()
            self.assertTrue(self.desktop.pending_id)
            self.assertTrue(self.desktop.automatic)
            self.settings['low_power'] = True
            self.desktop.configure()
            self.assertFalse(self.desktop.pending_id)

    def test_pausing_automatic_trip_cleans_up_before_arrival_or_next_phase(self):
        for before_arrival in (True, False):
            with self.subTest(before_arrival=before_arrival):
                self.pet.paused = False
                self.desktop.visit('invoice.xlsx')
                self.desktop.automatic = True
                if not before_arrival:
                    self.pet.land()
                self.pet.paused = True
                if before_arrival:
                    self.pet.land()
                else:
                    self.desktop.follow_window()
                self.assertFalse(self.desktop.pending_id or self.desktop.active_id)
                self.assertFalse(self.desktop.return_timer.isActive())
                self.assertFalse(self.desktop.action_timer.isActive())
                self.assertFalse(self.desktop.follow_timer.isActive())

    def test_leaving_stops_every_activity_timer_and_clears_queued_pose(self):
        self.desktop.visit('invoice.xlsx', 'dust')
        self.pet.land()
        self.pet.queued_action = ('DUST', 3)
        self.assertTrue(self.desktop.action_timer.isActive())
        self.desktop.emerge()
        for timer in (self.desktop.action_timer, self.desktop.return_timer,
                      self.desktop.peek_timer, self.desktop.follow_timer):
            self.assertFalse(timer.isActive())
        self.assertFalse(self.desktop.active_id or self.desktop.pending_id)
        self.assertIsNone(self.pet.action_state)
        self.assertIsNone(self.pet.queued_action)
        self.assertIsNone(self.desktop.anchor)
        self.assertTrue(self.pet.drop_motion)


if __name__ == '__main__':
    unittest.main()
