"""User-controlled memory: inspect, edit and forget locally saved preferences."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QCheckBox, QLineEdit, QComboBox, QPushButton, QListWidget, QListWidgetItem,
    QMessageBox)
from ui_style import apply_window_style
from memory import MAX_TEXT


class MemoryWindow(QDialog):
    preferences_changed = Signal(object)
    changed = Signal()
    home_requested = Signal()

    def __init__(self, store, settings):
        super().__init__()
        self.store, self.settings = store, settings
        self.editing_id = None
        self.setWindowTitle("Jeffery's Memory")
        self.resize(620, 580)
        layout = QVBoxLayout(self)
        title = QLabel("What Jeffery remembers")
        title.setStyleSheet("font-size: 21px; font-weight: 600;")
        heading = QHBoxLayout()
        heading.addWidget(title, 1)
        home = QPushButton("Home")
        home.clicked.connect(self.home_requested.emit)
        heading.addWidget(home)
        layout.addLayout(heading)
        hint = QLabel("Preferences stay on this computer. Edit or forget them any time. Daily tasks stay connected to your notebook.")
        hint.setWordWrap(True)
        hint.setObjectName("subtitle")
        layout.addWidget(hint)
        self.enabled = QCheckBox("Learn preferences from my messages and daily tasks")
        self.enabled.setChecked(settings["memory_enabled"])
        self.enriched = QCheckBox("Use AI to identify preferences in my messages")
        self.enriched.setToolTip("Messages are sent to Groq when this option is on.")
        self.enriched.setChecked(settings["memory_ai"])
        self.sharing = QCheckBox("Use saved preferences in AI replies")
        self.sharing.setToolTip("Relevant saved preferences are shared with Groq.")
        self.sharing.setChecked(settings["ai_share_memory"])
        for control in (self.enabled, self.enriched, self.sharing):
            layout.addWidget(control)
            control.toggled.connect(self.update_preferences)
        preferences_hint = QLabel("Memory options save immediately. Choose a memory below to edit or forget it.")
        preferences_hint.setObjectName("subtitle")
        preferences_hint.setWordWrap(True)
        layout.addWidget(preferences_hint)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search remembered preferences…")
        self.search.textChanged.connect(self.refresh)
        layout.addWidget(self.search)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self.select_entry)
        layout.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.kind = QComboBox()
        for label, value in (("Like", "like"), ("Dislike", "dislike"), ("Fact", "fact"), ("Routine", "routine")):
            self.kind.addItem(label, value)
        self.text = QLineEdit()
        self.text.setMaxLength(MAX_TEXT)
        self.text.setPlaceholderText("For example: mint tea, or exercise after work")
        row.addWidget(self.kind)
        row.addWidget(self.text, 1)
        layout.addLayout(row)
        actions = QHBoxLayout()
        self.action_buttons = {}
        for label, method in (("Save memory", self.save_entry), ("New", self.new_entry),
                              ("Forget selected", self.forget_entry), ("Clear memory", self.clear_memory)):
            button = QPushButton(label)
            button.clicked.connect(method)
            self.action_buttons[label] = button
            if label == "Save memory":
                button.setObjectName("primary")
            elif label in ("Forget selected", "Clear memory"):
                button.setObjectName("danger")
            actions.addWidget(button)
        self.action_buttons["Forget selected"].setEnabled(False)
        layout.addLayout(actions)
        self.status = QLabel("")
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        self.status.setObjectName("subtitle")
        layout.addWidget(self.status)
        self.configure()
        self.refresh()

    def configure(self):
        apply_window_style(self, self.settings)
        for control, key in ((self.enabled, "memory_enabled"), (self.enriched, "memory_ai"), (self.sharing, "ai_share_memory")):
            previous = control.blockSignals(True)
            control.setChecked(self.settings[key])
            control.blockSignals(previous)

    def update_preferences(self):
        self.preferences_changed.emit({"memory_enabled": self.enabled.isChecked(),
            "memory_ai": self.enriched.isChecked(), "ai_share_memory": self.sharing.isChecked()})

    def refresh(self, *args):
        selected = self.editing_id
        blocked = self.list.blockSignals(True)
        self.list.clear()
        for entry in self.store.search_entries(query=self.search.text()):
            item = QListWidgetItem(f"{entry['kind'].title()}: {entry['text']}")
            item.setData(Qt.UserRole, entry['id'])
            self.list.addItem(item)
            if entry['id'] == selected:
                self.list.setCurrentItem(item)
        self.list.blockSignals(blocked)
        if selected and self.list.currentItem() is None:
            self.editing_id = None
            self.text.clear()
        self.action_buttons["Forget selected"].setEnabled(bool(self.editing_id))
        self.action_buttons["Clear memory"].setEnabled(bool(self.store.entries()))
        if getattr(self.store, "warning", ""):
            self.status.setText(self.store.warning)

    def select_entry(self, item, previous=None):
        if item is None:
            return
        identifier = item.data(Qt.UserRole)
        entry = next((value for value in self.store.entries() if value['id'] == identifier), None)
        if entry:
            self.editing_id = identifier
            self.kind.setCurrentIndex(max(0, self.kind.findData(entry['kind'])))
            self.text.setText(entry['text'])
            self.action_buttons["Forget selected"].setEnabled(True)

    def new_entry(self):
        self.editing_id = None
        self.list.clearSelection()
        self.text.clear()
        self.action_buttons["Forget selected"].setEnabled(False)
        self.text.setFocus()

    def save_entry(self):
        try:
            if self.editing_id:
                self.store.edit(self.editing_id, self.kind.currentData(), self.text.text())
            else:
                self.store.remember(self.kind.currentData(), self.text.text())
            self.changed.emit()
            self.new_entry()
            self.refresh()
            self.status.setText("Memory saved on this computer.")
        except (OSError, ValueError) as exc:
            self.status.setText(str(exc))

    def forget_entry(self):
        if self.editing_id:
            try:
                self.store.forget(self.editing_id)
                self.changed.emit()
                self.new_entry()
                self.refresh()
                self.status.setText("Memory forgotten.")
            except (OSError, ValueError) as exc:
                self.status.setText(str(exc))

    def clear_memory(self):
        if QMessageBox.question(self, "Forget memories", "Remove all saved preferences and routines? Your notebook will stay intact.") != QMessageBox.Yes:
            return
        try:
            self.store.clear()
            self.changed.emit()
            self.new_entry()
            self.refresh()
            self.status.setText("Saved memory cleared. Turn learning off to stop collecting new memories.")
        except (OSError, ValueError) as exc:
            self.status.setText(str(exc))
