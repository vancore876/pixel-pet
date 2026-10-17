"""Additional desktop moves keep their own motion and manual-action contracts."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from characters import ANIMATION_STATES, CHARACTER_CHOICES, COMPANION_ANIMATIONS, draw_character
from pet import PixelPet
from settings import AppSettings
from tools.export_pet_sprites import MOBILE_STATES


ADDED_STATES = ("CARRY_BOX", "STOCK_OK", "FIND_SHELF", "PAPER_PLANE",
                "BLOW_BUBBLES", "TAI_CHI", "STARGAZE", "TAP_FOOT")
PREVIOUS_COMPANION_STATES = ("HEAD_TILT", "SNIFF", "LISTEN", "KNOCK", "DUST",
                             "INSPECT", "PERCH", "WING_FLAP", "FETCH", "CURL_UP")


def render(character, state, frame, direction=1, size=96, padding=4):
    image = QImage(size + padding * 2, size + padding * 2, QImage.Format_RGBA8888)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    try:
        painter.translate(padding, padding)
        draw_character(painter, size, frame=frame, state=state, character=character,
                       direction=direction, gaze=(-0.73, 0.41))
    finally:
        painter.end()
    return image


def pixels(image):
    return bytes(image.constBits())


class MoreCompanionAnimationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def test_appended_poses_preserve_mobile_and_existing_desktop_coordinates(self):
        self.assertEqual(ANIMATION_STATES[55:65], PREVIOUS_COMPANION_STATES)
        self.assertEqual(ANIMATION_STATES[65:73], ADDED_STATES)
        self.assertEqual(tuple(state for _, state in COMPANION_ANIMATIONS[:10]),
                         PREVIOUS_COMPANION_STATES)
        self.assertEqual(tuple(state for _, state in COMPANION_ANIMATIONS[10:18]),
                         ADDED_STATES)
        self.assertEqual(len(MOBILE_STATES), 49)
        self.assertEqual(MOBILE_STATES, ANIMATION_STATES[:49])
        self.assertEqual(len(ANIMATION_STATES), len(set(ANIMATION_STATES)))

    def test_pose_specific_props_move_apart_from_breathing_and_blinking(self):
        # Crops avoid the eyes/antenna; a static prop cannot pass by relying on
        # the character's ordinary blink or idle tail animation.
        regions = {"CARRY_BOX": (8, 22, 19, 8), "STOCK_OK": (22, 10, 9, 13),
                   "FIND_SHELF": (17, 23, 3, 7), "PAPER_PLANE": (22, 6, 9, 10),
                   "BLOW_BUBBLES": (24, 6, 7, 14), "TAI_CHI": (6, 15, 5, 10),
                   "STARGAZE": (21, 16, 10, 10), "TAP_FOOT": (17, 27, 6, 5)}
        for _, character in CHARACTER_CHOICES:
            for state, (x, y, width, height) in regions.items():
                with self.subTest(character=character, state=state):
                    crops = set()
                    for frame in (0.25, 3.75, 8.25, 14.5):
                        image = render(character, state, frame)
                        crops.add(pixels(image.copy(4 + x * 3, 4 + y * 3,
                                                    width * 3, height * 3)))
                    self.assertGreater(len(crops), 2)

    def test_late_loop_phases_stay_inside_every_character_footprint(self):
        for _, character in CHARACTER_CHOICES:
            for state in ADDED_STATES:
                for size in (16, 48, 96, 157):
                    for direction in (-1, 1):
                        for frame in (27.375, 101.125, 999.875):
                            with self.subTest(character=character, state=state, size=size,
                                              direction=direction, frame=frame):
                                image = render(character, state, frame, direction, size)
                                for rect in ((0, 0, size + 8, 4), (0, size + 4, size + 8, 4),
                                             (0, 4, 4, size), (size + 4, 4, 4, size)):
                                    self.assertFalse(any(pixels(image.copy(*rect))[3::4]),
                                                     "The pose must not be clipped by the widget")

    def test_pose_motion_uses_fractional_frames(self):
        for _, character in CHARACTER_CHOICES:
            for state in ADDED_STATES:
                with self.subTest(character=character, state=state):
                    self.assertNotEqual(pixels(render(character, state, 5.1)),
                                        pixels(render(character, state, 5.4)))

    def make_pet(self):
        temp = tempfile.TemporaryDirectory()
        settings = AppSettings(Path(temp.name) / "settings.json")
        settings.values.update({"mouse_mode": "off", "follow_mouse": False,
                                "reactions": False, "speech": False})
        pet = PixelPet(settings)
        pet.show()
        pet.timer.stop()

        def cleanup():
            pet.hide()
            pet.bubble.hide()
            pet.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            temp.cleanup()

        self.addCleanup(cleanup)
        return pet, settings

    def test_manual_new_moves_hold_position_and_complete_when_paused(self):
        pet, settings = self.make_pet()
        pet.set_paused(True)
        location = pet.pos()
        for state in ADDED_STATES:
            with self.subTest(state=state):
                with patch("pet.time.monotonic", return_value=100):
                    pet.interact(state)
                duration = 6 if state in ("TAI_CHI", "BLOW_BUBBLES", "STARGAZE") else 4
                self.assertEqual(pet.state, state)
                self.assertEqual(pet.action_until, 100 + duration)
                with patch("pet.time.monotonic", return_value=100.5):
                    pet.animate()
                self.assertEqual(pet.pos(), location)
                self.assertGreater(pet.frame, 0)
                self.assertEqual(pet.timer.interval(), 33)
                with patch("pet.time.monotonic", return_value=100 + duration + 0.1):
                    pet.animate()
                self.assertIsNone(pet.action_state)
                self.assertEqual(pet.state, "IDLE")
                self.assertEqual(pet.timer.interval(), 1000)
                self.assertFalse(pet.bubble.isVisible())

    def test_quiet_low_power_moves_do_not_start_speech_or_claim_stock_changes(self):
        pet, settings = self.make_pet()
        settings.values.update({"speech": True, "quiet_mode": True, "low_power": True})
        pet.ai_speech_connected = True
        requested = []
        pet.conversation_requested.connect(lambda *args: requested.append(args))
        for state in ADDED_STATES:
            pet.interact(state)
            self.assertEqual(pet.state, state)
            self.assertEqual(pet.timer.interval(), 200)
        self.assertFalse(requested)
        self.assertFalse(pet.bubble.isVisible())
        # Manual stock-themed props must not pretend a real order was changed.
        settings.values["quiet_mode"] = False
        pet.ai_speech_connected = False
        said = []
        pet.say = said.append
        for state in ("CARRY_BOX", "STOCK_OK", "FIND_SHELF"):
            pet.interact(state)
        self.assertEqual(len(said), 3)
        for line in said:
            self.assertFalse(any(claim in line.lower() for claim in
                                 ("stock saved", "stock updated", "order delivered", "i found")))


if __name__ == "__main__":
    unittest.main()
