"""Bounds and trust boundaries for chat actions, credential storage, and play."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_chat import parse_reply, validate_action
from credentials import CredentialStore, redact
from folder_play import FolderStore
from letter_play import LetterTile
from motion import HopMotion
from settings import AppSettings


class ActionTests(unittest.TestCase):
    def test_only_registered_actions_and_arguments(self):
        action = validate_action({"name": "buddy_action", "arguments": json.dumps({"action": "letters", "text": "HELLO"})})
        self.assertEqual(action["text"], "HELLO")
        for function in ({"name": "shell", "arguments": "{}"},
                         {"name": "buddy_action", "arguments": '{"action":"delete_file"}'},
                         {"name": "buddy_action", "arguments": '{"action":"wave","command":"anything"}'}):
            with self.assertRaises(ValueError):
                validate_action(function)

    def test_note_text_and_reminder_are_validated(self):
        note = validate_action({"name": "buddy_action", "arguments": '{"action":"draft_note","body":"Call the customer","repeat_minutes":5}'})
        self.assertEqual(note["repeat_minutes"], 5)
        for args in ('{"action":"draft_note"}', '{"action":"draft_note","body":"note","repeat_minutes":true}', '{"action":"letters","text":{}}'):
            with self.assertRaises(ValueError):
                validate_action({"name": "buddy_action", "arguments": args})

    def test_api_reply_retains_calls_and_ignores_reasoning(self):
        data = {"choices": [{"message": {"content": "I'm ready.", "reasoning": "Private model reasoning", "tool_calls": [{"id": "a1", "function": {"name": "buddy_action", "arguments": '{"action":"wave"}'}}]}}]}
        reply = parse_reply(200, json.dumps(data))
        self.assertEqual(reply["content"], "I'm ready.")
        self.assertNotIn("reasoning", reply)
        self.assertEqual(reply["tool_calls"][0]["id"], "a1")

    def test_errors_are_readable_and_do_not_echo_key(self):
        for status in (401, 403, 404, 429, 500):
            with self.assertRaises(ValueError) as failure:
                parse_reply(status, '{"key":"gsk_never_show_this_key"}')
            self.assertNotIn("gsk_", str(failure.exception))
        with self.assertRaises(ValueError):
            parse_reply(200, '{"choices":[]}')

    def test_bounded_response_and_action_count(self):
        reply = parse_reply(200, json.dumps({"choices": [{"message": {"content": "x" * 25000}}]}))
        self.assertEqual(len(reply["content"]), 20000)
        calls = [{"id": str(n), "function": {"name": "buddy_action", "arguments": "{}"}} for n in range(9)]
        with self.assertRaises(ValueError):
            parse_reply(200, json.dumps({"choices": [{"message": {"tool_calls": calls}}]}))


class CredentialTests(unittest.TestCase):
    def test_session_key_is_not_written_and_is_redacted(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"GROQ_API_KEY": ""}):
            path = Path(folder) / "groq.key"
            store = CredentialStore(path)
            key = "gsk_" + "testvalue" * 4
            store.save(key, remember=False)
            self.assertEqual(store.get(), key)
            self.assertFalse(path.exists())
            self.assertNotIn(key, redact("message " + key))
            store.forget()
            self.assertEqual(store.get(), "")

    def test_protected_save_and_recovery_use_dpapi(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"GROQ_API_KEY": ""}), patch("credentials.sys.platform", "win32"):
            path = Path(folder) / "groq.key"
            key = "gsk_" + "placeholder" * 3
            with patch("credentials._dpapi", return_value=b"protected-placeholder"):
                CredentialStore(path).save(key)
            self.assertNotIn(key.encode(), path.read_bytes())
            with patch("credentials._dpapi", return_value=key.encode()) as decrypt:
                self.assertEqual(CredentialStore(path).get(), key)
                decrypt.assert_called_once_with(b"protected-placeholder", decrypt=True)

    def test_settings_do_not_accept_credentials(self):
        validated = AppSettings.validate({"groq_api_key": "gsk_placeholder", "api_key": "example", "mouse_mode": "invalid", "ai_model": "bad\nmodel"})
        self.assertNotIn("groq_api_key", validated)
        self.assertNotIn("api_key", validated)
        self.assertEqual(validated["mouse_mode"], "watch")
        self.assertEqual(validated["ai_model"], "openai/gpt-oss-20b")


class PlayTests(unittest.TestCase):
    def test_hop_travels_in_an_arc_and_finishes_exactly(self):
        hop = HopMotion(10, 100, 210, 100)
        points = [hop.step(0.1) for _ in range(11)]
        self.assertLess(points[4][1], 40)
        self.assertEqual(points[-1], (210, 100, True))

    def test_letter_impulse_and_screen_bounds(self):
        tile = LetterTile("J", 20, 90)
        self.assertTrue(tile.impulse(30, 100))
        self.assertFalse(tile.impulse(900, 900))
        tile.vx, tile.vy = -350, -350
        for _ in range(100):
            tile.step(0.05, 800, 600)
            self.assertGreaterEqual(tile.x, 0)
            self.assertGreaterEqual(tile.y, 68)
            self.assertLessEqual(tile.x, 756)
            self.assertLessEqual(tile.y, 546)

    def test_real_folder_portals_persist_and_duplicate_paths_are_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "folders.json"
            store = FolderStore(path)
            portal = store.add(folder, [40, 80])
            self.assertEqual(store.add(folder)["id"], portal["id"])
            self.assertEqual(len(store.folders), 2)
            self.assertEqual(FolderStore(path).folders[-1]["position"], [40, 80])
            with self.assertRaises(ValueError):
                store.add(Path(folder) / "missing")

