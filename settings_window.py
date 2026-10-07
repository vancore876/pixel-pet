"""Accessible settings with explicit Apply/Cancel behavior."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTabWidget,
    QWidget, QFormLayout, QCheckBox, QSpinBox, QComboBox, QDialogButtonBox,
    QLineEdit, QScrollArea, QPushButton, QFileDialog, QMessageBox, QApplication)
from pathlib import Path
import json
import sys
from themes import palette
from settings import AppSettings

STYLE = """
QDialog { background: #131b28; color: #e4edf9; }
QWidget { color: #e4edf9; font-family: 'Segoe UI'; font-size: 13px; }
QLabel#subtitle { color: #9eafc5; }
QTabWidget::pane { border: 1px solid #334158; border-radius: 8px; background: #192333; }
QTabBar::tab { background: #131b28; padding: 10px 22px; color: #9eafc5; }
QTabBar::tab:selected { color: #83dbaf; background: #192333; }
QCheckBox { spacing: 8px; padding: 4px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #64758e; border-radius: 4px; background: #131b28; }
QCheckBox::indicator:checked { background: #83dbaf; border: 1px solid #83dbaf; }
QSpinBox, QComboBox, QLineEdit { background: #111a28; border: 1px solid #40516a; border-radius: 5px; padding: 5px 9px; min-width: 90px; }
QComboBox QAbstractItemView { background: #192333; selection-background-color: #315448; }
QPushButton { padding: 8px 18px; border: 1px solid #40516a; border-radius: 6px; background: #243246; }
QPushButton:hover { background: #35485f; }
QPushButton:default { background: #2c6655; border-color: #83dbaf; }
QWidget:disabled { color: #607088; }
QScrollArea { border: 0; background: transparent; }
QWidget#settingsPage, QWidget#scrollViewport { background: #192333; }
QTabBar QToolButton { background: #192333; border: 1px solid #334158; color: #e4edf9; }
QScrollBar:vertical { background: #192333; width: 10px; }
QScrollBar::handle:vertical { background: #40516a; border-radius: 4px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""


class SettingsWindow(QDialog):
    apply_requested = Signal(object)

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.setWindowTitle("PixelSystem Buddy · Settings")
        self.setMinimumWidth(540)
        self.setMinimumHeight(400)
        self.resize(560, min(600, QApplication.primaryScreen().availableGeometry().height() - 80))
        self.configure()
        self.controls = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        title = QLabel("Make yourself at home")
        title.setStyleSheet("font-size: 22px; font-weight: 600; padding-bottom: 6px;")
        layout.addWidget(title)
        subtitle = QLabel("A quiet monitor. A little company.")
        subtitle.setObjectName("subtitle")
        layout.addWidget(subtitle)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        general = self.tab("General")
        self.checkbox(general, "always_on_top", "Keep monitor and buddy on top")
        self.checkbox(general, "launch_minimized", "Launch with monitor hidden")
        self.checkbox(general, "low_power", "Low power mode: slower sampling and animation")
        self.checkbox(general, "quiet_mode", "Quiet mode: hide speech and focus notifications")
        startup = self.checkbox(general, "start_with_windows", "Start with Windows")
        startup.setEnabled(sys.platform == "win32")
        self.hint(general, "Startup uses your account only; no administrator access needed.")
        backup = QHBoxLayout()
        export = QPushButton("Export Settings")
        export.clicked.connect(self.export_settings)
        load = QPushButton("Import Settings")
        load.clicked.connect(self.import_settings)
        backup.addWidget(export)
        backup.addWidget(load)
        general.addRow(backup)
        self.hint(general, "Imported preferences are a draft until you click Apply. Startup and saved positions are kept local.")
        overlay = self.tab("Overlay")
        for key, label in (("show_cpu", "CPU"), ("show_ram", "Memory"), ("show_disk", "Disk"), ("show_network", "Network"), ("show_gpu", "GPU, when supported")):
            self.checkbox(overlay, key, label)
        self.spinbox(overlay, "opacity", "Opacity", 35, 100, " %")
        self.spinbox(overlay, "interval_ms", "Refresh interval", 500, 5000, " ms", 500)
        self.checkbox(overlay, "compact", "Compact layout")
        self.checkbox(overlay, "mini_hud", "Tiny HUD: 224 pixels wide")
        self.combobox(overlay, "monitor_style", "Monitor style", [("Medieval keep", "medieval"), ("Classic", "classic")])
        self.hint(overlay, "The keep's torch follows CPU activity. Quiet and low power modes keep the decoration still.")
        self.checkbox(overlay, "graphs", "Show 60-second graphs")
        self.checkbox(overlay, "show_system_info", "Show uptime and process count")
        self.checkbox(overlay, "show_battery", "Show battery when the PC has one")
        self.checkbox(overlay, "click_through", "Pass clicks through the monitor")
        hint = QLabel("To unlock click-through: right-click the buddy or tray icon.")
        hint.setObjectName("subtitle")
        overlay.addRow(hint)
        pet = self.tab("Buddy")
        self.checkbox(pet, "pet_enabled", "Show desktop buddy")
        name = QLineEdit()
        name.setMaxLength(20)
        self.controls["pet_name"] = name
        pet.addRow("Buddy name", name)
        self.combobox(pet, "character", "Character", [("Robot", "robot"), ("Cat", "cat"), ("Knight", "knight")])
        self.combobox(pet, "pet_palette", "Character color", [("Mint", "mint"), ("Sky", "sky"), ("Amber", "amber"), ("Rose", "rose")])
        self.combobox(pet, "roaming_mode", "Walking area", [("Bottom of screen", "bottom"), ("Free Buddy: whole screen", "free")])
        self.spinbox(pet, "pet_size", "Buddy size", 48, 160, " px", 24)
        self.spinbox(pet, "speed", "Walking speed", 5, 150, " px/s", 5)
        self.checkbox(pet, "speech", "Speech bubbles")
        self.spinbox(pet, "speech_frequency", "Random message interval", 30, 1800, " sec", 30)
        self.checkbox(pet, "reactions", "React to CPU and memory activity")
        self.checkbox(pet, "follow_active_monitor", "Walk on the monitor containing the mouse")
        self.checkbox(pet, "follow_mouse", "Walk toward the mouse")
        self.checkbox(pet, "parachute", "Open a parachute after dragging and dropping")
        self.checkbox(pet, "playful", "Random dances, flips, skating, juggling and more")
        self.combobox(pet, "mouse_mode", "Mouse play", [("Off", "off"), ("Watch and greet", "watch"), ("Chase the cursor", "chase"), ("Shy: run away", "shy")])
        self.checkbox(pet, "desktop_enabled", "Interact with real Windows folders, tabs and text")
        self.checkbox(pet, "desktop_random", "Randomly visit visible folders and tab edges")
        self.checkbox(pet, "folder_play", "Allow optional folder portal toys")
        self.checkbox(pet, "portal_toys", "Show the separate folder portal toys")
        self.spinbox(pet, "hide_seconds", "Hide-and-seek duration", 3, 120, " sec")
        self.hint(pet, "Parachuting lands at the bottom in either walking mode. Turn it off to keep a dragged height in Free Buddy mode. Click Jeffery to pet him; use his menu to feed or play.")
        look = self.tab("Appearance")
        self.combobox(look, "theme", "Theme", [("Midnight", "midnight"), ("Forest", "forest"), ("Plum", "plum")])
        self.hint(look, "The theme updates settings, the classic monitor, process window, and speech bubble together. Choose the medieval monitor in Overlay.")
        alerts = self.tab("Alerts")
        self.spinbox(alerts, "cpu_alert", "CPU warning level", 40, 100, " %")
        self.spinbox(alerts, "ram_alert", "Memory warning level", 40, 100, " %")
        self.spinbox(alerts, "alert_duration", "CPU must stay high for", 1, 60, " sec")
        self.spinbox(alerts, "alert_cooldown", "Repeat warning cooldown", 30, 900, " sec", 30)
        self.hint(alerts, "Warnings use buddy speech and activity reactions. Quiet mode silences them. Memory warnings do not require a sustained delay.")
        focus = self.tab("Focus")
        self.spinbox(focus, "focus_minutes", "Session length", 1, 120, " min", 5)
        self.hint(focus, "Start, pause/resume, or reset from the Focus Timer menu. The remaining time appears on the monitor; completion shows a break reminder.")
        notes = self.tab("Notes")
        self.checkbox(notes, "note_reminders", "Remind me about notes")
        self.spinbox(notes, "note_repeat_minutes", "Default repeat interval", 0, 1440, " min", 5)
        self.hint(notes, "Open Jeffery's Notepad from his menu. New notes get an acknowledgment, then repeat at their own interval. Set 0 for an acknowledgment without repeats. Quiet mode holds reminders until you turn it off. Jeffery must stay running.")
        documents = self.tab("Documents & memory")
        self.combobox(documents, "pdf_engine", "PDF reader", [("Automatic", "auto"),
            ("Native PDFium", "pdfium"), ("Compatibility (pypdf)", "pypdf")])
        self.hint(documents, "Use Compatibility if a complex PDF cannot be read by the native reader. OCR requires the native reader.")
        self.checkbox(documents, "pdf_ocr", "Read scanned PDF pages with OCR by default")
        self.hint(documents, "You can choose OCR for each import in Notepad. Scanned pages take longer; install Tesseract and its language files first.")
        for key, label, placeholder in (("ocr_language", "OCR languages", "eng or eng+fra"),
                ("tesseract_path", "Tesseract executable", "Automatic detection, or select tesseract.exe"),
                ("semantic_model_path", "Local memory model folder", "Default: data/models/all-MiniLM-L6-v2")):
            control = QLineEdit()
            control.setMaxLength(64 if key == "ocr_language" else 1024)
            control.setPlaceholderText(placeholder)
            self.controls[key] = control
            if key == "ocr_language":
                documents.addRow(label, control)
            else:
                row = QHBoxLayout()
                row.addWidget(control, 1)
                browse = QPushButton("Browse…")
                browse.clicked.connect(lambda checked=False, field=key: self.choose_local_path(field))
                row.addWidget(browse)
                documents.addRow(label, row)
        self.checkbox(documents, "semantic_memory_enabled", "Find related notebook memories by meaning")
        self.semantic_status = QLabel("Meaning-based recall is optional. Choose a downloaded local model before enabling it.")
        self.semantic_status.setObjectName("subtitle")
        self.semantic_status.setTextFormat(Qt.PlainText)
        self.semantic_status.setWordWrap(True)
        documents.addRow(self.semantic_status)
        self.hint(documents, "Optional local model: install the semantic dependencies and download the model using the README commands. No model downloads happen during chat. Indexing runs in the background; keyword search stays available while it catches up. Sharing notes with Groq also shares relevant recalled passages.")
        footer = QHBoxLayout()
        self.status = QLabel("")
        self.status.setObjectName("subtitle")
        footer.addWidget(self.status, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Apply).clicked.connect(self.apply)
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons)
        layout.addLayout(footer)

    def tab(self, label):
        page = QWidget()
        page.setObjectName("settingsPage")
        form = QFormLayout(page)
        form.setContentsMargins(18, 16, 18, 16)
        form.setSpacing(8)
        scroll = QScrollArea()
        scroll.viewport().setObjectName("scrollViewport")
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        self.tabs.addTab(scroll, label)
        return form

    def choose_local_path(self, key):
        if key == "semantic_model_path":
            selected = QFileDialog.getExistingDirectory(self, "Choose downloaded memory model")
        else:
            selected, _ = QFileDialog.getOpenFileName(self, "Choose Tesseract executable", "", "Executables (*.exe);;All files (*)")
        if selected:
            self.controls[key].setText(selected)

    def configure(self):
        colors = palette(self.settings)
        style = STYLE
        for original, replacement in {"#131b28": colors["bg"], "#192333": colors["panel"],
                "#334158": colors["border"], "#40516a": colors["border"],
                "#e4edf9": colors["text"], "#9eafc5": colors["muted"],
                "#83dbaf": colors["accent"]}.items():
            style = style.replace(original, replacement)
        self.setStyleSheet(style)

    def hint(self, form, text):
        label = QLabel(text)
        label.setObjectName("subtitle")
        label.setWordWrap(True)
        form.addRow(label)

    def combobox(self, form, key, text, choices):
        widget = QComboBox()
        for label, value in choices:
            widget.addItem(label, value)
        form.addRow(text, widget)
        self.controls[key] = widget

    def checkbox(self, form, key, text):
        widget = QCheckBox(text)
        form.addRow(widget)
        self.controls[key] = widget
        return widget

    def spinbox(self, form, key, text, low, high, suffix, step=1):
        widget = QSpinBox()
        widget.setRange(low, high)
        widget.setSuffix(suffix)
        widget.setSingleStep(step)
        form.addRow(text, widget)
        self.controls[key] = widget

    def refresh(self, tab=0):
        for key, widget in self.controls.items():
            if isinstance(widget, QCheckBox):
                widget.setChecked(self.settings[key])
            elif isinstance(widget, QComboBox):
                widget.setCurrentIndex(widget.findData(self.settings[key]))
            elif isinstance(widget, QLineEdit):
                widget.setText(self.settings[key])
            else:
                widget.setValue(self.settings[key])
        self.status.clear()
        self.tabs.setCurrentIndex(tab)

    def apply(self):
        values = {key: widget.isChecked() if isinstance(widget, QCheckBox) else widget.currentData() if isinstance(widget, QComboBox) else widget.text() if isinstance(widget, QLineEdit) else widget.value() for key, widget in self.controls.items()}
        self.apply_requested.emit(values)

    def export_settings(self):
        file, _ = QFileDialog.getSaveFileName(self, "Export saved preferences", "BuddySettings.json", "JSON (*.json)")
        if file:
            try:
                preferences = {key: value for key, value in self.settings.values.items() if not key.endswith("_position") and key != "start_with_windows"}
                Path(file).write_text(json.dumps(preferences, indent=2) + "\n", encoding="utf-8")
                self.status.setText("Saved preferences exported.")
            except OSError as exc:
                QMessageBox.warning(self, "Export settings", str(exc))

    def load_draft(self, path):
        path = Path(path)
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("This settings file is too large.")
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("The settings file must contain a JSON object.")
        merged = dict(self.settings.values)
        merged.update({key: value for key, value in raw.items() if key in self.controls and key != "start_with_windows"})
        validated = AppSettings.validate(merged)
        for key, widget in self.controls.items():
            if isinstance(widget, QCheckBox):
                widget.setChecked(validated[key])
            elif isinstance(widget, QComboBox):
                widget.setCurrentIndex(widget.findData(validated[key]))
            elif isinstance(widget, QLineEdit):
                widget.setText(validated[key])
            else:
                widget.setValue(validated[key])
        self.status.setText("Imported draft. Apply to save.")

    def import_settings(self):
        file, _ = QFileDialog.getOpenFileName(self, "Import preferences", "", "JSON (*.json)")
        if file:
            try:
                self.load_draft(file)
            except (OSError, ValueError, UnicodeError) as exc:
                QMessageBox.warning(self, "Import settings", str(exc))
