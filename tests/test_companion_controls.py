"""Desktop companion choices, smooth timing, and icon-only action routing."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QCoreApplication, QEvent, QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QPixmap
from PySide6.QtWidgets import QApplication

from ai_brain import parse_behavior
from ai_chat import play_requested, validate_action
from characters import CHARACTER_CHOICES, COMPANION_ANIMATIONS
from config import DEFAULTS
from pet import PixelPet
from settings import AppSettings
from settings_window import SettingsWindow

APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)


class CompanionControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = AppSettings(Path(self.temp.name) / 'settings.json')
        self.settings.values.update({'mouse_mode': 'off', 'follow_mouse': False,
            'reactions': False, 'speech': False, 'business_mode': False})
        self.pet = PixelPet(self.settings)
        self.pet.show()
        self.pet.timer.stop()

    def tearDown(self):
        self.pet.hide()
        self.pet.bubble.hide()
        self.pet.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.temp.cleanup()

    def test_all_character_choices_save_reload_and_render_in_settings(self):
        window = SettingsWindow(self.settings)
        try:
            choices = window.controls['character']
            for label, key in CHARACTER_CHOICES:
                with self.subTest(character=key):
                    index = choices.findData(key)
                    self.assertGreaterEqual(index, 0)
                    self.assertEqual(choices.itemText(index), label)
                    choices.setCurrentIndex(index)
                    self.settings.save({'character': choices.currentData()})
                    self.assertEqual(AppSettings(self.settings.path)['character'], key)
                    self.pet.configure()
                    self.pet.timer.stop()
                    self.pet.perform('HEAD_TILT')
                    self.assertEqual(self.pet.loaded_character, key)
                    self.assertFalse(self.pet.grab().isNull())
            self.assertEqual(AppSettings.validate({'character': '../outside'})['character'], 'robot')
        finally:
            window.deleteLater()

    def walk_for_one_second(self, step, low_power):
        self.settings.values['low_power'] = low_power
        self.pet.paused, self.pet.action_state = False, None
        self.pet.real_x = 100.0
        self.pet.real_y = float(self.pet.monitor_rect().bottom() + 1 - self.pet.height())
        self.pet.state, self.pet.direction = 'WALK_RIGHT', 1
        self.pet.frame, self.pet.last_tick, self.pet.next_state = 0.0, 100.0, 10000
        for tick in range(1, round(1 / step) + 1):
            with patch('pet.time.monotonic', return_value=100 + tick * step):
                self.pet.animate()
        return self.pet.real_x, self.pet.frame

    def test_smooth_and_low_power_motion_keep_same_speed_and_animation_duration(self):
        smooth = self.walk_for_one_second(1 / 30, False)
        self.assertEqual(self.pet.timer.interval(), 33)
        economy = self.walk_for_one_second(0.2, True)
        self.assertEqual(self.pet.timer.interval(), 200)
        self.assertAlmostEqual(smooth[0], 100 + DEFAULTS['speed'])
        self.assertAlmostEqual(smooth[1], 10)
        self.assertAlmostEqual(economy[0], smooth[0])
        self.assertAlmostEqual(economy[1], smooth[1])

    def test_resting_and_pause_keep_work_low_and_do_not_accumulate_hidden_time(self):
        self.pet.state, self.pet.last_tick, self.pet.next_state = 'SLEEP', 100, 10000
        self.settings.values['low_power'] = True
        self.pet.frame = 0
        with patch('pet.time.monotonic', return_value=100.4):
            self.pet.animate()
        self.assertAlmostEqual(self.pet.frame, 4)
        self.assertEqual(self.pet.timer.interval(), 400)
        self.pet.set_paused(True)
        before = self.pet.frame
        with patch('pet.time.monotonic', return_value=101.4):
            self.pet.animate()
        self.assertEqual(self.pet.frame, before)
        self.assertEqual(self.pet.timer.interval(), 1000)
        self.pet.hide()
        self.assertFalse(self.pet.timer.isActive())
        self.pet.show()
        self.assertTrue(self.pet.timer.isActive())
        self.assertNotEqual(self.pet.last_tick, 101.4)

    def test_new_manual_poses_and_fractional_custom_sprite_playback(self):
        for _, state in COMPANION_ANIMATIONS:
            self.pet.interact(state)
            self.assertEqual(self.pet.action_state, state)
            self.assertEqual(self.pet.timer.interval(), 33)
            self.assertFalse(self.pet.bubble.isVisible())
        first, second = QPixmap(16, 16), QPixmap(16, 16)
        first.fill(Qt.red)
        second.fill(Qt.blue)
        self.pet.sprites['IDLE'] = [first, second]
        self.pet.state, self.pet.frame = 'IDLE', 1.5
        image = self.pet.grab().toImage()
        self.assertEqual(image.pixelColor(image.width() // 2, image.height() // 2), Qt.blue)

    def test_dragged_file_routes_to_visible_target_without_stat_or_network_read(self):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile('//office-server/share/stock.xlsx')])
        enter = QDragEnterEvent(QPoint(12, 12), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        drop = QDropEvent(QPointF(12, 12), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        routed = []
        self.pet.folder_dropped.connect(lambda path, position: routed.append(path))
        with patch.object(Path, 'stat', side_effect=AssertionError('GUI must not stat a work share')):
            self.pet.dragEnterEvent(enter)
            self.pet.dropEvent(drop)
        self.assertTrue(enter.isAccepted())
        self.assertTrue(drop.isAccepted())
        self.assertEqual(routed, [mime.urls()[0].toLocalFile()])
        mime.setUrls([QUrl('https://example.com/report.pdf')])
        remote = QDragEnterEvent(QPoint(12, 12), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        self.pet.dragEnterEvent(remote)
        self.assertFalse(remote.isAccepted())

    def test_file_play_requires_visible_file_and_rejects_file_operations(self):
        file = {'id': 'file:42:1,2', 'kind': 'file'}
        action = {'action': 'inspect_file', 'target_id': file['id']}
        self.assertEqual(parse_behavior(json.dumps(action), [file])['action'], 'inspect_file')
        for targets in ([], [{**file, 'kind': 'folder'}]):
            with self.assertRaises(ValueError):
                parse_behavior(json.dumps(action), targets)
        for action_name in ('open_file', 'delete_file', 'move_file', 'read_file'):
            with self.assertRaises(ValueError):
                validate_action({'name': 'buddy_action', 'arguments': json.dumps({'action': action_name})})
        self.assertTrue(play_requested('Please inspect the stock.xlsx file icon'))
        self.assertFalse(play_requested("Don't inspect my file icon"))
        self.assertFalse(play_requested('Inspect this PDF and explain the invoice'))
        self.assertFalse(play_requested('What is a file icon?'))


if __name__ == '__main__':
    unittest.main()
