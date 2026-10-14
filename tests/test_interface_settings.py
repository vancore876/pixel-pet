"""Workspace appearance and dialog drafts remain separate from desktop colors."""
from pathlib import Path
import tempfile
import unittest

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication
from settings import AppSettings
from settings_window import SettingsWindow


app = QApplication.instance() or QApplication([])
app.setQuitOnLastWindowClosed(False)
if app.platformName() == "offscreen" and Path("C:/Windows/Fonts/segoeui.ttf").is_file():
    # The Windows offscreen backend does not load installed fonts itself.
    # Use the same real font as the desktop instead of its oversized fallback.
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeuib.ttf")


class InterfaceSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = AppSettings(Path(self.temp.name) / "settings.json")
        self.window = SettingsWindow(self.settings)

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.temp.cleanup()

    def test_upgrade_defaults_and_roundtrip_preserve_desktop_theme(self):
        defaults = AppSettings.validate({"theme": "forest"})
        self.assertEqual(defaults["interface_appearance"], "light")
        self.assertTrue(defaults["home_on_start"])
        self.assertEqual(defaults["theme"], "forest")
        self.settings.save({"interface_appearance": "dark", "home_on_start": False, "theme": "plum"})
        loaded = AppSettings(self.settings.path)
        self.assertEqual(loaded["interface_appearance"], "dark")
        self.assertFalse(loaded["home_on_start"])
        self.assertEqual(loaded["theme"], "plum")
        invalid = AppSettings.validate({"interface_appearance": "unknown", "home_on_start": "yes"})
        self.assertEqual(invalid["interface_appearance"], "light")
        self.assertTrue(invalid["home_on_start"])

    def test_navigation_keeps_drafts_and_cancel_does_not_save(self):
        emitted = []
        self.window.apply_requested.connect(emitted.append)
        self.window.controls["home_on_start"].setChecked(False)
        self.window.controls["pet_name"].setText("Counter Buddy")
        self.window.navigation.setCurrentRow(3)
        self.window.controls["interface_appearance"].setCurrentIndex(1)
        self.window.navigation.setCurrentRow(2)
        self.assertEqual(self.window.controls["pet_name"].text(), "Counter Buddy")
        self.assertFalse(self.window.controls["home_on_start"].isChecked())
        self.window.reject()
        self.assertFalse(emitted)
        self.assertEqual(self.settings["pet_name"], "Jeffery")
        self.assertTrue(self.settings["home_on_start"])
        self.window.refresh(2)
        self.assertEqual(self.window.navigation.currentRow(), 2)
        self.assertEqual(self.window.controls["pet_name"].text(), "Jeffery")
        self.assertEqual(self.window.controls["interface_appearance"].currentData(), "light")

    def test_apply_emits_current_draft_without_discarding_other_controls(self):
        self.settings.save({"theme": "forest"})
        self.window.refresh()
        emitted = []
        self.window.apply_requested.connect(emitted.append)
        self.window.controls["pet_name"].setText("Jeffery Jr")
        self.window.controls["interface_appearance"].setCurrentIndex(2)
        self.window.controls["home_on_start"].setChecked(False)
        self.window.apply()
        self.assertEqual(emitted[0]["theme"], "forest")
        self.assertEqual(emitted[0]["interface_appearance"], "system")
        self.assertFalse(emitted[0]["home_on_start"])
        self.assertEqual(emitted[0]["pet_name"], "Jeffery Jr")
        self.assertEqual(self.settings["pet_name"], "Jeffery")

    def test_all_sections_fit_at_800_by_600_with_vertical_scroll(self):
        self.window.resize(800, 600)
        self.window.show()
        app.processEvents()
        self.assertEqual(self.window.width(), 800)
        self.assertEqual(self.window.height(), 600)
        base_style = self.window.styleSheet()
        for font_size in (13, 16):
            self.window.setStyleSheet(base_style.replace("font-size: 13px", f"font-size: {font_size}px"))
            for index in range(self.window.tabs.count()):
                self.window.navigation.setCurrentRow(index)
                app.processEvents()
                scroll = self.window.tabs.currentWidget()
                self.assertEqual(scroll.horizontalScrollBar().maximum(), 0,
                    f"{self.window.page_labels[index]}, {font_size}px")
                self.assertLessEqual(scroll.width(), self.window.width())
                self.assertTrue(self.window.buttons.isVisible())
                self.assertTrue(self.window.rect().contains(self.window.buttons.geometry()))


if __name__ == "__main__":
    unittest.main()
