"""Render the renewed utility dialogs and exercise their existing actions."""
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication
from settings import AppSettings
from settings_window import SettingsWindow
from memory import MemoryStore
from memory_window import MemoryWindow
from process_window import ProcessWindow
from launcher import QuickLauncher, LauncherWindow


app = QApplication.instance() or QApplication([])
app.setQuitOnLastWindowClosed(False)
if app.platformName() == "offscreen" and Path("C:/Windows/Fonts/segoeui.ttf").is_file():
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeuib.ttf")
preview = Path(tempfile.gettempdir()) / "famous-twins-interface-preview"
preview.mkdir(exist_ok=True)

with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    settings = AppSettings(root / "settings.json")
    settings_window = SettingsWindow(settings)
    memory = MemoryWindow(MemoryStore(root / "memory.json"), settings)
    process = ProcessWindow(settings)
    launcher = QuickLauncher(root / "shortcuts.json")
    shortcuts = LauncherWindow(launcher, settings)
    windows = (settings_window, memory, process, shortcuts)
    try:
        settings_window.resize(800, 600)
        settings_window.show()
        settings_window.refresh(3)
        app.processEvents()
        settings_window.grab().save(str(preview / "settings-light.png"))
        settings_window.refresh(2)
        app.processEvents()
        settings_window.grab().save(str(preview / "buddy-settings-light.png"))

        memory.show()
        memory.text.setText("Opening shift starts at 8 am")
        memory.kind.setCurrentIndex(memory.kind.findData("routine"))
        memory.save_entry()
        assert memory.list.count() == 1
        assert "saved" in memory.status.text().lower()
        app.processEvents()
        memory.grab().save(str(preview / "memory-light.png"))

        process.show()
        process.accept_rows([
            {"name": "Jeffery", "cpu": 1.2, "memory": 150 * 1024 * 1024, "pid": 123},
            {"name": "Parts catalog", "cpu": 4.8, "memory": 240 * 1024 * 1024, "pid": 456},
        ])
        assert process.table.item(0, 0).text() == "Parts catalog"
        app.processEvents()
        process.grab().save(str(preview / "pc-activity-light.png"))

        shortcuts.show()
        launcher.store.add("Parts supplier", "url", "https://example.com/parts")
        launcher.changed.emit()
        app.processEvents()
        assert shortcuts.scroll.horizontalScrollBar().maximum() == 0
        shortcuts.grab().save(str(preview / "shortcuts-light.png"))

        settings.save({"interface_appearance": "dark"})
        for window in windows:
            window.configure()
        settings_window.refresh(3)
        app.processEvents()
        settings_window.grab().save(str(preview / "settings-dark.png"))
        print(f"PASS: sidebar drafts, memory edit, PC activity, shortcut layout; previews in {preview}")
    finally:
        for window in windows:
            window.close()
            window.deleteLater()
        launcher.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
