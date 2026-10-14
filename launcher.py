"""User-chosen app, folder, and website shortcuts; no shell command strings."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import uuid
from PySide6.QtCore import QObject, Signal, QProcess, QUrl, QStandardPaths
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QComboBox, QLineEdit, QFileDialog, QMessageBox, QScrollArea, QWidget)
from ui_style import apply_window_style, ui_palette


class ShortcutStore:
    def __init__(self, path):
        self.path = Path(path)
        self.shortcuts = []
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(raw, list):
                    raise ValueError("Expected a shortcut list")
                for item in raw[:30]:
                    try:
                        self.shortcuts.append(self.validate(item))
                    except ValueError:
                        continue
            except (OSError, ValueError, UnicodeError):
                try:
                    self.path.replace(self.path.with_suffix(".corrupt.json"))
                except OSError:
                    pass

    @staticmethod
    def validate(item):
        if not isinstance(item, dict):
            raise ValueError("Invalid shortcut")
        label, target, kind = item.get("label"), item.get("target"), item.get("kind")
        if not isinstance(label, str) or not label.strip() or not isinstance(target, str) or not target.strip():
            raise ValueError("Give the shortcut a name and a target.")
        if kind not in ("app", "folder", "url"):
            raise ValueError("Choose App, Folder, or Website.")
        target = target.strip()
        if kind == "url":
            url = QUrl(target)
            if url.scheme() not in ("http", "https") or not url.host():
                raise ValueError("Use a complete http:// or https:// website address.")
        elif not Path(target).is_absolute():
            raise ValueError("Choose a full path to the app or folder.")
        if kind == "app" and Path(target).suffix.lower() in (".bat", ".cmd", ".ps1", ".sh"):
            raise ValueError("Choose the application's executable, such as Code.exe.")
        identifier = item.get("id")
        return {"id": identifier if isinstance(identifier, str) and identifier else uuid.uuid4().hex,
                "label": "".join(c for c in label if c.isprintable()).strip()[:40], "target": target[:4096], "kind": kind}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.shortcuts, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, self.path)

    def add(self, label, kind, target):
        if len(self.shortcuts) >= 30:
            raise ValueError("You can save up to 30 shortcuts.")
        item = self.validate({"label": label, "kind": kind, "target": target})
        self.shortcuts.append(item)
        try:
            self.save()
        except OSError:
            self.shortcuts.pop()
            raise
        return item

    def remove(self, identifier):
        previous = list(self.shortcuts)
        self.shortcuts = [s for s in self.shortcuts if s["id"] != identifier]
        try:
            self.save()
        except OSError:
            self.shortcuts = previous
            raise


def start_app(path, arguments=None):
    result = QProcess.startDetached(str(path), list(arguments or []))
    success = result[0] if isinstance(result, tuple) else result
    if not success:
        raise OSError("The application could not be started. Check its path.")


class QuickLauncher(QObject):
    changed = Signal()
    error = Signal(str)
    notepad_requested = Signal()

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.store = ShortcutStore(path)

    def launch(self, identifier):
        try:
            if identifier == "notepad":
                self.notepad_requested.emit()
                return
            if identifier == "browser":
                target = QUrl("https://www.google.com")
            elif identifier == "documents":
                folder = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
                target = QUrl.fromLocalFile(folder)
            elif identifier == "vscode":
                paths = [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Microsoft VS Code" / "Code.exe",
                         Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Microsoft VS Code" / "Code.exe"] if sys.platform == "win32" else []
                executable = next((str(p) for p in paths if p.is_file()), None)
                candidate = shutil.which("code")
                if not executable and candidate and Path(candidate).suffix.lower() not in (".cmd", ".bat"):
                    executable = candidate
                if not executable:
                    raise OSError("VS Code was not found. Add a shortcut to its Code.exe in Quick Launch.")
                start_app(executable)
                return
            else:
                item = next((s for s in self.store.shortcuts if s["id"] == identifier), None)
                if not item:
                    raise OSError("This shortcut no longer exists.")
                if item["kind"] == "app":
                    if not Path(item["target"]).is_file():
                        raise OSError("The application was moved or removed. Update its shortcut.")
                    start_app(item["target"])
                    return
                if item["kind"] == "folder" and not Path(item["target"]).is_dir():
                    raise OSError("This folder no longer exists.")
                target = QUrl(item["target"]) if item["kind"] == "url" else QUrl.fromLocalFile(item["target"])
            if not QDesktopServices.openUrl(target):
                raise OSError("The operating system could not open this shortcut.")
        except (OSError, ValueError) as exc:
            self.error.emit(str(exc))


class LauncherWindow(QDialog):
    home_requested = Signal()

    def __init__(self, launcher, settings):
        super().__init__()
        self.launcher, self.settings = launcher, settings
        self.setWindowTitle("Jeffery · Quick Launch")
        self.resize(540, 520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        title = QLabel("Your tools, one click away")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        heading = QHBoxLayout()
        heading.addWidget(title, 1)
        home = QPushButton("Home")
        home.clicked.connect(self.home_requested.emit)
        heading.addWidget(home)
        layout.addLayout(heading)
        self.scroll = QScrollArea()
        self.scroll.viewport().setObjectName("launcherViewport")
        self.scroll.setWidgetResizable(True)
        layout.addWidget(self.scroll, 1)
        add_title = QLabel("Add your own app, project folder, or website")
        layout.addWidget(add_title)
        self.label = QLineEdit()
        self.label.setPlaceholderText("Shortcut name")
        self.label.setMaxLength(40)
        layout.addWidget(self.label)
        target_row = QHBoxLayout()
        self.kind = QComboBox()
        for label, key in (("App", "app"), ("Folder", "folder"), ("Website", "url")):
            self.kind.addItem(label, key)
        self.target = QLineEdit()
        self.target.setPlaceholderText("Full path or https:// address")
        self.target.setMaxLength(4096)
        browse = QPushButton("Browse")
        browse.clicked.connect(self.browse)
        target_row.addWidget(self.kind)
        target_row.addWidget(self.target, 1)
        target_row.addWidget(browse)
        layout.addLayout(target_row)
        add = QPushButton("Add shortcut")
        add.setObjectName("primary")
        add.clicked.connect(self.add_shortcut)
        layout.addWidget(add)
        self.status = QLabel("Shortcuts open only when you click them.")
        self.status.setObjectName("hint")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        launcher.changed.connect(self.refresh)
        self.configure()
        self.refresh()

    def configure(self):
        apply_window_style(self, self.settings)
        c = ui_palette(self.settings)
        self.setStyleSheet(self.styleSheet() + f"QScrollArea, QWidget#shortcutGrid, QWidget#launcherViewport {{ background: {c['bg']}; border: 0; }}")

    def refresh(self):
        panel = QWidget()
        panel.setObjectName("shortcutGrid")
        grid = QGridLayout(panel)
        entries = [("Browser", "browser"), ("VS Code", "vscode"), ("Notebook", "notepad"), ("Documents", "documents")]
        entries.extend((s["label"], s["id"]) for s in self.launcher.store.shortcuts)
        for index, (label, key) in enumerate(entries):
            row = QHBoxLayout()
            button = QPushButton(label)
            button.setMinimumHeight(40)
            button.clicked.connect(lambda checked=False, identifier=key: self.launcher.launch(identifier))
            row.addWidget(button, 1)
            if index >= 4:
                remove = QPushButton("×")
                remove.setFixedWidth(40)
                remove.setToolTip(f"Remove {label}")
                remove.setAccessibleName(f"Remove {label}")
                remove.clicked.connect(lambda checked=False, identifier=key: self.remove_shortcut(identifier))
                row.addWidget(remove)
            grid.addLayout(row, index // 2, index % 2)
        grid.setRowStretch(len(entries) // 2 + 1, 1)
        self.scroll.setWidget(panel)

    def browse(self):
        if self.kind.currentData() == "url":
            self.target.setFocus()
            return
        if self.kind.currentData() == "folder":
            target = QFileDialog.getExistingDirectory(self, "Choose a project folder")
        else:
            target, _ = QFileDialog.getOpenFileName(self, "Choose an application", "", "Applications (*.exe);;All files (*)" if sys.platform == "win32" else "All files (*)")
        if target:
            self.target.setText(target)
            if not self.label.text():
                self.label.setText(Path(target).stem)

    def add_shortcut(self):
        try:
            self.launcher.store.add(self.label.text(), self.kind.currentData(), self.target.text())
            self.launcher.changed.emit()
            self.label.clear()
            self.target.clear()
            self.status.setText("Shortcut saved.")
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Add shortcut", str(exc))

    def remove_shortcut(self, identifier):
        try:
            self.launcher.store.remove(identifier)
            self.launcher.changed.emit()
        except OSError as exc:
            QMessageBox.warning(self, "Remove shortcut", str(exc))
