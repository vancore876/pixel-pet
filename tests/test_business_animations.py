"""Business poses remain readable, local, grounded and quiet when requested."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from business_voice import BusinessVoice
from characters import ANIMATION_STATES, BUSINESS_ANIMATIONS, draw_character
from pet import PixelPet
from settings import AppSettings
from tools.export_pet_sprites import MOBILE_STATES


LEGACY_STATES = ("IDLE", "WALK_LEFT", "WALK_RIGHT", "SLEEP", "EXCITED", "DRAG",
                 "PARACHUTE", "LANDING", "WAVE", "PET", "EAT", "DANCE", "JUMP",
                 "YAWN", "READ", "REMINDER", "CELEBRATE", "HOP", "PEEK",
                 "CHASE", "SHY", "PUSH", "THINK", "TALK", "LOOK", "STRETCH",
                 "SPIN", "FLIP", "ROLL", "SNEEZE", "SCARED", "LAUGH", "SIT",
                 "BALANCE", "TIPTOE", "RUN", "CLIMB", "SLIDE", "SKATE", "BOUNCE",
                 "MAGIC", "UMBRELLA", "JUGGLE", "GROOM", "SALUTE", "FACEPALM",
                 "LEAN", "HANG", "SNEAK")


def rendered(character, state, size=96, frame=0, direction=1, padding=0):
    image = QImage(size + padding * 2, size + padding * 2, QImage.Format_RGBA8888)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    try:
        painter.translate(padding, padding)
        draw_character(painter, size, frame=frame, state=state,
                       character=character, direction=direction)
    finally:
        painter.end()
    return image


def pixels(image):
    return bytes(image.constBits())


class Credentials:
    def get(self):
        return ''


class Client(QObject):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self):
        super().__init__()
        self.calls = []

    def send(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return True

    def cancel(self):
        pass


class BusinessAnimationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = AppSettings(Path(self.temp.name) / 'settings.json')
        self.settings.values.update({'mouse_mode': 'off', 'follow_mouse': False,
                                     'reactions': False, 'speech': True,
                                     'business_mode': True, 'business_name': 'Famous Twins',
                                     'playful': True, 'quiet_mode': False, 'low_power': False})
        self.pet = None
        self.voice = None

    def tearDown(self):
        if self.voice:
            self.voice.stop()
        if self.pet:
            self.pet.timer.stop()
            self.pet.close()
            self.pet.bubble.close()
        self.temp.cleanup()

    def make_pet(self):
        self.pet = PixelPet(self.settings)
        self.pet.show()
        self.pet.timer.stop()
        return self.pet

    def make_voice(self, notes=()):
        self.spoken = []
        self.client = Client()
        self.voice = BusinessVoice(self.settings, Credentials(), lambda *_: {},
                                   lambda: notes, self.spoken.append, lambda: True,
                                   client=self.client)
        return self.voice

    def test_original_state_order_and_unique_new_states(self):
        self.assertEqual(MOBILE_STATES, LEGACY_STATES)
        self.assertEqual(ANIMATION_STATES[:49], LEGACY_STATES)
        self.assertEqual(ANIMATION_STATES[49:], tuple(state for _, state in BUSINESS_ANIMATIONS))
        self.assertEqual(len(set(ANIMATION_STATES)), 55)

    def test_every_pose_has_visible_animation_and_fits_all_characters_and_sizes(self):
        for character in ('robot', 'cat', 'knight'):
            for size in (48, 96, 160):
                idle = pixels(rendered(character, 'IDLE', size))
                for _, state in BUSINESS_ANIMATIONS:
                    with self.subTest(character=character, size=size, state=state):
                        frames = [rendered(character, state, size, frame) for frame in (0, 4, 9, 15)]
                        self.assertNotEqual(pixels(frames[0]), idle)
                        # Props occupy the right/front of each pose, away from the
                        # antenna and eyes whose motion is shared by legacy poses.
                        unit = max(1, size // 32)
                        offset = (size - unit * 32) // 2
                        region = {'CHECK_STOCK': (20, 16, 11, 15), 'SCAN_PART': (16, 18, 15, 13),
                                  'PACK_ORDER': (10, 21, 14, 10), 'WRENCH': (20, 16, 11, 15),
                                  'HIGH_FIVE': (24, 9, 7, 12), 'COFFEE': (20, 17, 11, 14)}[state]
                        x, y, width, height = region
                        y_offset = size - unit * 32
                        crops = {pixels(image.copy(offset + x * unit, y_offset + y * unit,
                                                   width * unit, height * unit)) for image in frames}
                        self.assertGreater(len(crops), 1, 'The business prop did not animate')
                        for direction in (-1, 1):
                            image = rendered(character, state, size, 9, direction, padding=4)
                            alpha = pixels(image)[3::4]
                            occupied = [i for i, value in enumerate(alpha) if value]
                            self.assertGreater(len(occupied), size * size * 0.06)
                            stride = image.width()
                            self.assertGreaterEqual(min(i % stride for i in occupied), 4)
                            self.assertLess(max(i % stride for i in occupied), size + 4)
                            self.assertGreaterEqual(min(i // stride for i in occupied), 4)
                            self.assertLess(max(i // stride for i in occupied), size + 4)

    def test_manual_interactions_use_new_states_and_grounded_speech(self):
        pet = self.make_pet()
        said = []
        pet.say = said.append
        for _, state in BUSINESS_ANIMATIONS:
            pet.interact(state)
            self.assertEqual(pet.action_state, state)
            self.assertEqual(pet.frame, 0)
            self.assertEqual(pet.timer.interval(), 100)
        self.assertIn('part number', said[1])
        self.assertIn('Famous Twins', said[4])
        self.assertFalse(any('I checked' in text or 'in stock' in text for text in said))
        before = pet.action_state
        pet.interact('NOT_AN_ANIMATION')
        self.assertEqual(pet.action_state, before)
        self.assertEqual(len(said), len(BUSINESS_ANIMATIONS))

    def test_manual_quiet_interaction_animates_without_requesting_speech(self):
        pet = self.make_pet()
        self.settings.values['quiet_mode'] = True
        self.settings.values['low_power'] = True
        pet.ai_speech_connected = True
        requested = []
        pet.conversation_requested.connect(lambda *args: requested.append(args))
        pet.interact('PACK_ORDER')
        self.assertEqual(pet.state, 'PACK_ORDER')
        self.assertEqual(pet.timer.interval(), 200)
        self.assertFalse(requested)
        self.assertFalse(pet.bubble.isVisible())

    def test_automatic_business_poses_respect_business_playful_quiet_and_power_settings(self):
        pet = self.make_pet()
        expected = {'CHECK_STOCK', 'SCAN_PART', 'PACK_ORDER', 'WRENCH'}
        for change, included in (({}, True), ({'business_mode': False}, False),
                                 ({'playful': False}, False), ({'quiet_mode': True}, False),
                                 ({'low_power': True}, False)):
            with self.subTest(change=change):
                self.settings.values.update({'business_mode': True, 'playful': True,
                                             'quiet_mode': False, 'low_power': False})
                self.settings.values.update(change)
                pet.next_state = 0
                with patch('pet.random.choices', return_value=['IDLE']) as choose:
                    pet.animate()
                choices, weights = choose.call_args.args
                self.assertEqual(expected.intersection(choices), expected if included else set())
                self.assertEqual(weights[:3], [7, 3, 0.5] if self.settings['playful'] else [7, 3, 1])
                if included:
                    self.assertEqual(weights[-4:], [0.18] * 4)
        pet.set_paused(True)
        with patch('pet.random.choices') as choose:
            pet.animate()
            choose.assert_not_called()
        self.assertEqual(pet.timer.interval(), 1000)

    def test_offline_action_suggestions_use_saved_counts_and_do_not_modify_orders(self):
        order = {'done': False, 'kind': 'order', 'order_status': 'ready', 'order_due': None,
                 'checklist': [{'done': False, 'text': 'Brake pads', 'quantity': 2}]}
        notes = [order]
        before = copy.deepcopy(notes)
        voice = self.make_voice(notes)
        self.settings.values['ai_greetings'] = False
        with patch('business_voice.random.choice', side_effect=lambda choices: choices[-1]):
            voice.request('interaction', 'PACK_ORDER')
            self.assertIn('1 saved order is marked ready', self.spoken[-1])
            voice.request('interaction', 'CHECK_STOCK')
            self.assertIn('1 unchecked item', self.spoken[-1])
        self.assertEqual(notes, before)
        self.assertFalse(self.client.calls)

    def test_failed_ai_keeps_action_context_and_quiet_mode_suppresses_late_fallback(self):
        voice = self.make_voice()
        voice.pending_event, voice.pending_detail = 'interaction', 'COFFEE'
        voice.failed('Network unavailable')
        self.assertTrue('coffee' in self.spoken[-1] or 'sip' in self.spoken[-1])
        self.settings.values['quiet_mode'] = True
        voice.failed('Network unavailable')
        voice.received({'content': 'This should not be spoken.'})
        self.assertEqual(len(self.spoken), 1)


if __name__ == '__main__':
    unittest.main()
