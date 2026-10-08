"""Shared office startup keeps user data private across launch and sign-in."""
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import settings
import work_buddy


class WorkLaunchTests(unittest.TestCase):
    def test_work_entrypoint_selects_private_profile_before_starting_app(self):
        def run_app():
            self.assertEqual(os.environ.get("JEFFERY_WORK_PROFILE"), "1")
            self.assertTrue(sys.dont_write_bytecode)
            return 7

        with patch.dict(os.environ, {}, clear=True), patch.object(sys, "dont_write_bytecode", False), \
                patch.dict(sys.modules, {"main": SimpleNamespace(main=run_app)}):
            self.assertEqual(work_buddy.main(), 7)

    def test_windows_startup_preserves_shared_profile_and_quotes_paths(self):
        registry = SimpleNamespace(CreateKey=MagicMock(), SetValueEx=Mock(),
                                   REG_SZ=1, HKEY_CURRENT_USER=object())
        root = Path(r"\\office-server\Shared Apps\Jeffery")
        executable = str(Path("C:/Users/Office User/Local Python/python.exe"))
        for mode, entrypoint in (("1", "work_buddy.py"), ("", "main.py")):
            with self.subTest(mode=mode), patch.dict(os.environ, {"JEFFERY_WORK_PROFILE": mode}), \
                    patch.dict(sys.modules, {"winreg": registry}), \
                    patch.object(settings.sys, "platform", "win32"), \
                    patch.object(settings.sys, "executable", executable), \
                    patch.object(settings.sys, "frozen", False, create=True), \
                    patch.object(settings, "ROOT", root), patch.object(Path, "exists", return_value=True):
                settings.set_windows_startup(True)
                command = registry.SetValueEx.call_args.args[-1]
                expected = [str(Path(executable).with_name("pythonw.exe")), str(root / entrypoint)]
                self.assertEqual(command, subprocess.list2cmdline(expected))
                self.assertIn('"', command)


if __name__ == "__main__":
    unittest.main()
