"""Normal workspaces use native window controls without changing close behavior."""
from contextlib import ExitStack
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QInputDialog,
                              QMainWindow, QMenu, QMessageBox, QWidget)
from ui_style import apply_window_style, apply_workspace_window_controls

APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)
BUTTONS = (Qt.WindowMinimizeButtonHint, Qt.WindowMaximizeButtonHint,
           Qt.WindowCloseButtonHint)


class WindowControlHelperTests(unittest.TestCase):
    def tearDown(self):
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_visible_window_keeps_visibility_and_state_when_controls_are_added(self):
        for state in (Qt.WindowNoState, Qt.WindowMaximized, Qt.WindowMinimized):
            with self.subTest(state=state):
                window = QDialog()
                try:
                    window.resize(540, 380)
                    window.setWindowState(state)
                    window.show()
                    APP.processEvents()
                    self.assertTrue(apply_workspace_window_controls(window))
                    APP.processEvents()
                    self.assertTrue(window.isVisible())
                    self.assertEqual(window.windowState(), state)
                    self.assertEqual(window.windowType(), Qt.Dialog)
                    for flag in BUTTONS:
                        self.assertTrue(window.windowFlags() & flag)
                    self.assertFalse(window.windowFlags() & Qt.WindowContextHelpButtonHint)
                    self.assertFalse(apply_workspace_window_controls(window))
                    self.assertEqual(window.windowState(), state)
                finally:
                    window.hide()
                    window.deleteLater()

    def test_main_windows_also_keep_their_window_type(self):
        window = QMainWindow()
        try:
            apply_window_style(window, {"interface_appearance": "light"})
            self.assertEqual(window.windowType(), Qt.Window)
            for flag in BUTTONS:
                self.assertTrue(window.windowFlags() & flag)
        finally:
            window.deleteLater()

    def test_confirmations_menus_and_passive_widgets_keep_their_flags(self):
        modal, frameless = QDialog(), QDialog()
        modal.setWindowModality(Qt.ApplicationModal)
        frameless.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint)
        child_owner = QWidget()
        child = QWidget(child_owner)
        windows = (modal, frameless, QMessageBox(), QFileDialog(), QInputDialog(),
                   QMenu(), QWidget(), child)
        try:
            for window in windows:
                with self.subTest(widget=type(window).__name__, flags=window.windowFlags()):
                    previous = window.windowFlags()
                    self.assertFalse(apply_workspace_window_controls(window))
                    self.assertEqual(window.windowFlags(), previous)
        finally:
            for window in windows[:-1]:
                window.deleteLater()
            child_owner.deleteLater()


class EmptyCredentials:
    def get(self):
        return ""


class WorkspaceWindowIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from main import BuddyApp
        from settings import AppSettings

        cls.resources = ExitStack()
        cls.root = Path(cls.resources.enter_context(tempfile.TemporaryDirectory(prefix="jeffery-window-controls-")))
        cls.resources.enter_context(patch.dict(os.environ, {
            "GROQ_API_KEY": "", "LOCALAPPDATA": str(cls.root / "local-appdata")}))
        cls.resources.enter_context(patch("main.CredentialStore", return_value=EmptyCredentials()))
        cls.resources.enter_context(patch("config.team_data_directory", return_value=cls.root / "team-profile"))
        cls.resources.enter_context(patch("work_notifications.team_data_directory", return_value=cls.root / "team-profile"))
        cls.settings = AppSettings(cls.root / "settings.json")
        cls.settings.values.update({"desktop_enabled": False, "mouse_mode": "off", "ai_autonomy": False,
            "memory_ai": False, "business_greetings": False, "home_on_start": False,
            "semantic_memory_enabled": False, "pet_enabled": False, "team_sync_enabled": False,
            "inventory_shared": False})
        cls.buddy = BuddyApp(APP, cls.settings, show_tray=False)
        cls.buddy.note_service.stop()
        cls.buddy.overlay.hide()
        cls.buddy.pet.hide()
        cls.windows = {"Home": cls.buddy.home, "Notepad": cls.buddy.notepad,
            "AI Chat": cls.buddy.chat, "Work Chat": cls.buddy.work_chat,
            "IT administration": cls.buddy.it_window,
            "Inventory": cls.buddy.inventory_window, "Settings": cls.buddy.dialog,
            "Memory": cls.buddy.memory_window, "PC activity": cls.buddy.process_window,
            "Launcher": cls.buddy.launcher_window, "Team messages": cls.buddy.team_window,
            "Desktop play": cls.buddy.desktop.panel, "Selected letters": cls.buddy.desktop.text_window}

    @classmethod
    def tearDownClass(cls):
        cls.buddy.shutdown()
        for window in cls.windows.values():
            window.hide()
            window.deleteLater()
        for window in (cls.buddy.overlay, cls.buddy.pet, cls.buddy.note_popup,
                       cls.buddy.team_popup, cls.buddy.work_popup, cls.buddy.letters):
            window.hide()
            window.deleteLater()
        cls.buddy.deleteLater()
        APP.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        cls.resources.close()

    def tearDown(self):
        for window in self.windows.values():
            window.hide()
            window.setWindowState(Qt.WindowNoState)

    def test_every_workspace_has_minimize_maximize_restore_and_close_controls(self):
        for label, window in self.windows.items():
            with self.subTest(workspace=label):
                self.assertEqual(window.windowType(), Qt.Dialog)
                self.assertFalse(window.windowFlags() & Qt.FramelessWindowHint)
                self.assertFalse(window.windowFlags() & Qt.WindowContextHelpButtonHint)
                for flag in BUTTONS:
                    self.assertTrue(window.windowFlags() & flag)
                window.showMaximized()
                APP.processEvents()
                self.assertTrue(window.isMaximized())
                window.showNormal()
                APP.processEvents()
                self.assertFalse(window.isMaximized())
                window.showMinimized()
                APP.processEvents()
                self.assertTrue(window.isMinimized())
                window.showNormal()
                APP.processEvents()
                self.assertFalse(window.isMinimized())
                window.close()
                self.assertFalse(window.isVisible())
                self.assertFalse(self.buddy.shutting_down)
                self.assertTrue(self.buddy.thread.isRunning())

    def test_theme_changes_keep_open_maximized_and_minimized_windows_in_place(self):
        for label, window in self.windows.items():
            for state in (Qt.WindowNoState, Qt.WindowMaximized, Qt.WindowMinimized):
                with self.subTest(workspace=label, state=state):
                    window.setWindowState(state)
                    window.show()
                    APP.processEvents()
                    for appearance in ("dark", "light"):
                        self.settings.values["interface_appearance"] = appearance
                        window.configure()
                        APP.processEvents()
                        self.assertTrue(window.isVisible())
                        self.assertEqual(window.windowState(), state)
                    window.hide()

    def test_close_keeps_writing_and_message_drafts_and_background_app_alive(self):
        notebook, work = self.buddy.notepad, self.buddy.work_chat
        notebook.title.setText("Unsaved counter note")
        notebook.body.setPlainText("Keep this writing when I close the workspace.")
        work.composer.setPlainText("Unsent customer pickup message")
        notebook.show()
        work.show()
        APP.processEvents()
        notebook.close()
        work.close()
        APP.processEvents()
        self.assertFalse(APP.quitOnLastWindowClosed())
        self.assertFalse(self.buddy.shutting_down)
        self.assertTrue(self.buddy.thread.isRunning())
        notebook.show()
        work.show()
        self.assertEqual(notebook.title.text(), "Unsaved counter note")
        self.assertEqual(notebook.body.toPlainText(), "Keep this writing when I close the workspace.")
        self.assertEqual(work.composer.toPlainText(), "Unsent customer pickup message")
        self.assertFalse(self.buddy.notes_store.notes)
        self.assertFalse(self.buddy.work_chat.token)


if __name__ == "__main__":
    unittest.main()
