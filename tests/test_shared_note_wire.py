import unittest
from shared_note_wire import encode_entry, decode_entry, PREFIX


class SharedNoteWireTests(unittest.TestCase):
    def test_round_trip_unicode_body_and_many_highlights(self):
        highlights = [f'PART-{number:03}' for number in range(200)]
        original = {'id': 'a' * 32, 'kind': 'note', 'body': 'Parts 😀\nSecond line', 'highlights': highlights}
        encoded = encode_entry(original)
        self.assertEqual(encoded['kind'], 'list')
        self.assertEqual(decode_entry(encoded), original)

    def test_oversize_note_is_not_truncated_or_modified(self):
        original = {'kind': 'note', 'body': 'x' * 9999, 'highlights': ['x']}
        with self.assertRaisesRegex(ValueError, 'too large'):
            encode_entry(original)
        self.assertEqual(len(original['body']), 9999)
        self.assertEqual(original['highlights'], ['x'])

    def test_malformed_compatibility_text_is_left_unchanged(self):
        for text in ('Not encoded', PREFIX + 'broken json', PREFIX + '[]'):
            original = {'kind': 'list', 'body': text}
            self.assertIs(decode_entry(original), original)
