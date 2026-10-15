"""Companions retain distinct anatomy, smooth motion and their desktop footprint."""
import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from characters import ANIMATION_STATES, CHARACTER_CHOICES, COMPANION_ANIMATIONS, draw_character


def rendered(character, state="IDLE", frame=4.25, size=96, height=None, direction=1,
             gaze=(0.2, -0.3), padding=4):
    height = height or size
    image = QImage(size + padding * 2, height + padding * 2, QImage.Format_RGBA8888)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    try:
        painter.translate(padding, padding)
        draw_character(painter, size, height=height, frame=frame, state=state,
                       character=character, direction=direction, gaze=gaze)
    finally:
        painter.end()
    return image


def pixels(image):
    return bytes(image.constBits())


class CharacterExpansionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def assert_inside_footprint(self, image, size, height=None, padding=4):
        height = height or size
        outside = ((0, 0, image.width(), padding),
                   (0, padding + height, image.width(), padding),
                   (0, padding, padding, height),
                   (padding + size, padding, padding, height))
        for rectangle in outside:
            self.assertFalse(any(pixels(image.copy(*rectangle))[3::4]),
                             "Painting outside the widget clips a pose on the desktop")
        self.assertGreater(sum(alpha > 30 for alpha in pixels(image)[3::4]),
                           size * size * 0.08)

    def test_catalog_preserves_old_character_ids_and_appends_ten_unique_poses(self):
        self.assertEqual(CHARACTER_CHOICES[:3], (("Robot", "robot"), ("Cat", "cat"),
                                               ("Knight", "knight")))
        self.assertEqual(tuple(value for _, value in CHARACTER_CHOICES[3:]),
                         ("dog", "fox", "owl", "dragon"))
        self.assertEqual(ANIMATION_STATES[55:], tuple(state for _, state in COMPANION_ANIMATIONS))
        self.assertEqual(len(set(ANIMATION_STATES)), len(ANIMATION_STATES))

    def test_all_poses_accept_fractional_time_and_gaze_for_every_character(self):
        for _, character in CHARACTER_CHOICES:
            for state in ANIMATION_STATES:
                with self.subTest(character=character, state=state):
                    image = rendered(character, state, frame=3.375, gaze=(0.43, -0.62))
                    self.assertTrue(any(pixels(image)[3::4]))

    def test_new_poses_fit_small_large_and_fractional_sizes_in_both_directions(self):
        for _, character in CHARACTER_CHOICES:
            for _, state in COMPANION_ANIMATIONS:
                for size in (16, 48, 96, 157):
                    for direction in (-1, 1):
                        for frame in (0, 3.25, 9.5, 15.75):
                            with self.subTest(character=character, state=state, size=size,
                                              direction=direction, frame=frame):
                                image = rendered(character, state, frame, size,
                                                 direction=direction)
                                self.assert_inside_footprint(image, size)

    def test_parachutes_keep_the_extra_canvas_and_do_not_clip_at_fractional_sizes(self):
        for _, character in CHARACTER_CHOICES:
            for size in (16, 48, 96, 157):
                height = round(size * 1.75)
                for direction in (-1, 1):
                    with self.subTest(character=character, size=size, direction=direction):
                        image = rendered(character, "PARACHUTE", 4.75, size, height, direction)
                        self.assert_inside_footprint(image, size, height)
                        canopy = image.copy(4, 4, size, max(1, height - size))
                        self.assertTrue(any(pixels(canopy)[3::4]))

    def test_each_new_pose_is_distinct_and_animates_for_each_character(self):
        for _, character in CHARACTER_CHOICES:
            idle = pixels(rendered(character, frame=0))
            poses = []
            for _, state in COMPANION_ANIMATIONS:
                with self.subTest(character=character, state=state):
                    frames = {pixels(rendered(character, state, frame=frame))
                              for frame in (0, 3.5, 9.25, 15.5)}
                    self.assertGreater(len(frames), 2)
                    first = pixels(rendered(character, state, frame=0))
                    self.assertNotEqual(first, idle)
                    poses.append(first)
            self.assertEqual(len(set(poses)), len(COMPANION_ANIMATIONS))

    def test_characters_have_distinct_silhouettes_and_lit_materials(self):
        silhouettes = {}
        size = 128
        for _, character in CHARACTER_CHOICES:
            image = rendered(character, size=size)
            raw = pixels(image)
            silhouettes[character] = {i for i, alpha in enumerate(raw[3::4]) if alpha > 180}
            colours = {raw[i:i + 3] for i in range(0, len(raw), 4) if raw[i + 3] > 180}
            self.assertGreater(len(colours), 200, "Shaded surfaces should survive desktop rendering")
        for left, left_mask in silhouettes.items():
            for right, right_mask in silhouettes.items():
                if left < right:
                    with self.subTest(left=left, right=right):
                        self.assertGreater(len(left_mask ^ right_mask), size * size * 0.02,
                                           "Characters need their own anatomy, not palette swaps")

    def test_breathing_and_eye_tracking_do_not_snap_to_whole_frames(self):
        for _, character in CHARACTER_CHOICES:
            with self.subTest(character=character):
                self.assertNotEqual(pixels(rendered(character, frame=4.1)),
                                    pixels(rendered(character, frame=4.3)))
                self.assertNotEqual(pixels(rendered(character, frame=4, gaze=(0.2, 0.1))),
                                    pixels(rendered(character, frame=4, gaze=(0.7, 0.5))))


if __name__ == "__main__":
    unittest.main()
