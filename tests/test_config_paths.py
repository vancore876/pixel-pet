"""Shared-office launch keeps each Windows user's settings off the app share."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
from settings import AppSettings


class DataDirectoryTests(unittest.TestCase):
    def test_work_profiles_keep_accounts_separate_without_writing_shared_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shared_app = root / "shared-app"
            shared_data = shared_app / "data"
            shared_data.mkdir(parents=True)
            shared_settings = shared_data / "settings.json"
            shared_settings.write_text(
                json.dumps({"work_chat_username": "shared_old_user", "pet_name": "Shared"}),
                encoding="utf-8",
            )
            before = {path.relative_to(shared_app): path.read_bytes()
                      for path in shared_app.rglob("*") if path.is_file()}
            paths = []
            with patch.object(config, "ROOT", shared_app):
                for username in ("alice", "bob"):
                    user_local = root / username / "AppData" / "Local"
                    with patch.dict(os.environ, {"JEFFERY_WORK_PROFILE": "1",
                                                 "LOCALAPPDATA": str(user_local)}):
                        settings = AppSettings()
                        self.assertEqual(settings.path, user_local / "PixelSystemBuddy" /
                                         "work-data" / "settings.json")
                        self.assertEqual(settings["work_chat_username"], "")
                        self.assertEqual(settings["pet_name"], config.DEFAULTS["pet_name"])
                        settings.save({"work_chat_username": username,
                                       "work_chat_server": "192.168.50.194"})
                        paths.append(settings.path)
                        self.assertEqual(AppSettings()["work_chat_username"], username)

            self.assertNotEqual(paths[0], paths[1])
            self.assertEqual(json.loads(paths[0].read_text(encoding="utf-8"))
                             ["work_chat_username"], "alice")
            self.assertEqual(json.loads(paths[1].read_text(encoding="utf-8"))
                             ["work_chat_username"], "bob")
            after = {path.relative_to(shared_app): path.read_bytes()
                     for path in shared_app.rglob("*") if path.is_file()}
            self.assertEqual(after, before)
            self.assertFalse((shared_data / ".write-check").exists())

    def test_work_profile_without_localappdata_uses_home_and_never_creates_app_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shared_app = root / "shared-app"
            shared_app.mkdir()
            home = root / "user-home"
            with patch.object(config, "ROOT", shared_app), \
                    patch.object(Path, "home", return_value=home), \
                    patch.dict(os.environ, {"JEFFERY_WORK_PROFILE": "1"}):
                os.environ.pop("LOCALAPPDATA", None)
                settings = AppSettings()
                settings.save({"work_chat_username": "alice"})
                self.assertEqual(settings.path, home / ".local" / "share" /
                                 "PixelSystemBuddy" / "work-data" / "settings.json")
            self.assertEqual(list(shared_app.iterdir()), [])

    def test_normal_launch_preserves_portable_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app_root = root / "app"
            with patch.object(config, "ROOT", app_root), \
                    patch.dict(os.environ, {"JEFFERY_WORK_PROFILE": "0",
                                           "LOCALAPPDATA": str(root / "local")}):
                settings = AppSettings()
                settings.save({"work_chat_username": "portable_user"})
                self.assertEqual(settings.path, app_root / "data" / "settings.json")
                self.assertEqual(AppSettings()["work_chat_username"], "portable_user")
            self.assertFalse((root / "local").exists())

    def test_normal_launch_keeps_local_fallback_when_portable_data_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app_root = root / "app-file"
            app_root.write_text("not a directory", encoding="utf-8")
            local = root / "local"
            with patch.object(config, "ROOT", app_root), \
                    patch.dict(os.environ, {"JEFFERY_WORK_PROFILE": "0",
                                           "LOCALAPPDATA": str(local)}):
                self.assertEqual(config.data_directory(), local / "PixelSystemBuddy")


if __name__ == "__main__":
    unittest.main()
