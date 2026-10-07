"""Real-target validation, DPI conversion, and non-editing AI decisions."""
import json
from pathlib import Path
import sys
import unittest
from PySide6.QtCore import QRect
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desktop_bridge import validate_targets, logical_rect, checked_rect
from ai_brain import parse_behavior
from ai_chat import play_requested
from sliding_text import plain_reply
from real_desktop import graphemes


def target(identifier='folder1', kind='folder'):
    return {'id': identifier, 'kind': kind, 'name': 'Projects', 'path': r'C:\Users\User\Desktop\Projects',
            'rect': [120, 200, 90, 100], 'hwnd': 2048, 'pid': 10, 'runtime': '42,10'}


class DesktopTests(unittest.TestCase):
    def test_bad_targets_and_duplicates_are_ignored(self):
        values = [target(), target(), {**target('bad'), 'rect': [1, 2, 0, 4]},
                  {**target('badtype'), 'kind': 'command'}, {**target('badowner'), 'pid': 0}]
        self.assertEqual(len(validate_targets({'targets': values})), 1)
        with self.assertRaises(ValueError):
            validate_targets({'targets': 'invalid'})

    def test_negative_monitor_and_mixed_dpi_coordinates(self):
        class Screen:
            def name(self): return 'DISPLAY2'
            def devicePixelRatio(self): return 2.0
            def geometry(self): return QRect(-1280, 0, 1280, 720)
        rect = logical_rect([-2400, 200, 100, 80], [{'name': 'DISPLAY2', 'rect': [-2560, 0, 2560, 1440]}], [Screen()])
        self.assertEqual(rect, QRect(-1200, 100, 50, 40))
        with self.assertRaises(ValueError): checked_rect([0, 0, True, 10])

    def test_ai_target_kind_must_match_current_visible_controls(self):
        targets = [target(), target('tab1', 'tab')]
        valid = parse_behavior('{"action":"hide","target_id":"folder1","say":"Found a hiding place."}', targets)
        self.assertEqual(valid['target_id'], 'folder1')
        for action, identifier in (('hide', 'tab1'), ('ride_tab', 'missing'), ('window_edge', 'folder1')):
            with self.assertRaises(ValueError):
                parse_behavior(json.dumps({'action': action, 'target_id': identifier}), targets)

    def test_autonomous_ai_cannot_edit_or_select_tabs(self):
        for action in ('replace_text', 'select_tab', 'draft_note', 'shell', 'letters'):
            with self.assertRaises(ValueError):
                parse_behavior(json.dumps({'action': action}), [])
        with self.assertRaises(ValueError):
            parse_behavior('{"action":"animate","animation":"MAGIC","command":"run"}', [])
        value = parse_behavior('{"action":"animate","animation":"JUGGLE","say":"**Tiny trick!**"}', [])
        self.assertEqual(value['say'], 'Tiny trick!')

    def test_plain_replies_remove_table_markup_and_html_breaks(self):
        value = plain_reply('**Hello**<br>| Item | Details |\n|---|---|\n| CPU | 12% |')
        self.assertEqual(value, 'Hello\nItem · Details\nCPU · 12%')
        self.assertEqual(plain_reply('Python: 2**3 and __init__'), 'Python: 2**3 and __init__')

    def test_unrelated_question_does_not_enable_play_tools(self):
        self.assertFalse(play_requested('axio 4wd drive front disc pad'))
        self.assertFalse(play_requested('How much memory am I using?'))
        self.assertFalse(play_requested('What is a pressure wave?'))
        self.assertFalse(play_requested("Don't wave at me"))
        self.assertTrue(play_requested('Please wave to me'))
        self.assertTrue(play_requested('Hide in my folder'))

    def test_combining_characters_stay_together_when_letters_move(self):
        self.assertEqual(graphemes('e\u0301 👩\u200d💻!'), ['e\u0301', ' ', '👩\u200d💻', '!'])
        self.assertEqual(graphemes('A\r\nB'), ['A', '\r\n', 'B'])


if __name__ == '__main__':
    unittest.main()
