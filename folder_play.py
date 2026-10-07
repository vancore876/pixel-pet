"""Movable folder portals: real folder shortcuts with animated hide-and-seek."""
from __future__ import annotations
import json
import os
from pathlib import Path
import uuid
from PySide6.QtCore import QObject, Signal, Qt, QTimer, QRectF, QUrl
from PySide6.QtGui import QPainter, QColor, QFont, QPen, QDesktopServices
from PySide6.QtWidgets import QApplication, QWidget, QMenu, QFileDialog
from config import clamp_position
from characters import draw_character
from themes import palette


class FolderStore:
    def __init__(self, path):
        self.path = Path(path)
        self.folders = [{"id": "den", "label": "Jeffery's Den", "path": "", "position": None}]
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(raw, list):
                    raise ValueError()
                self.folders = []
                for item in raw[:8]:
                    if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                        continue
                    label, target = item.get("label"), item.get("path")
                    if not isinstance(label, str) or not isinstance(target, str) or not label.strip():
                        continue
                    pos = item.get("position")
                    if not isinstance(pos, list) or len(pos) != 2 or not all(type(v) is int and abs(v) < 100000 for v in pos):
                        pos = None
                    self.folders.append({"id": item["id"][:64], "label": label.strip()[:80], "path": target[:4096], "position": pos})
            except (OSError, ValueError, UnicodeError):
                try:
                    self.path.replace(self.path.with_suffix(".corrupt.json"))
                except OSError:
                    pass

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.folders, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, self.path)

    def add(self, path, position=None):
        path = Path(path).expanduser().resolve()
        if not path.is_dir():
            raise ValueError("Choose an existing folder.")
        existing = next((f for f in self.folders if f["path"] == str(path)), None)
        if existing:
            return existing
        if len(self.folders) >= 8:
            raise ValueError("You can have up to eight folder hideouts.")
        folder = {"id": uuid.uuid4().hex, "label": path.name or str(path), "path": str(path), "position": position}
        self.folders.append(folder)
        try:
            self.save()
        except OSError:
            self.folders.pop()
            raise
        return folder

    def position(self, identifier, position):
        item = next((f for f in self.folders if f["id"] == identifier), None)
        if item:
            previous = item["position"]
            item["position"] = position
            try:
                self.save()
            except OSError:
                item["position"] = previous
                raise

    def remove(self, identifier):
        previous = list(self.folders)
        self.folders = [f for f in self.folders if f["id"] != identifier]
        try:
            self.save()
        except OSError:
            self.folders = previous
            raise


class FolderCard(QWidget):
    jump_requested = Signal(str)
    emerge_requested = Signal()
    remove_requested = Signal(str)
    position_changed = Signal(str, object)
    error = Signal(str)

    def __init__(self, folder, settings):
        super().__init__()
        self.folder, self.settings = folder, settings
        self.occupied, self.peeking = False, False
        self.drag_offset, self.dragged = None, False
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("Jeffery hideout · " + folder["label"])
        self.setToolTip("Click: hide or come out · Double-click: open folder · Drag: move hideout")
        self.setFixedSize(128, 138)
        self.configure()
        rect = QApplication.primaryScreen().availableGeometry()
        position = folder["position"] or [rect.left() + 28, rect.bottom() - 260]
        self.move(clamp_position(position, self.width(), self.height(), QApplication.screens()))

    def configure(self):
        visible = self.isVisible()
        flags = Qt.Tool | Qt.FramelessWindowHint
        if self.settings["always_on_top"]:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        if visible:
            self.show()
        self.update()

    def destination(self, pet):
        return [self.x() + (self.width() - pet.width()) // 2, self.y() + 40 - pet.height() // 3]

    def paintEvent(self, event):
        c = palette(self.settings)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(c["border"]), 1))
        p.setBrush(QColor(c["panel"]))
        p.drawRoundedRect(QRectF(1, 1, 126, 136), 12, 12)
        p.setRenderHint(QPainter.Antialiasing, False)
        p.fillRect(23, 27, 32, 12, QColor("#ffd38c"))
        p.fillRect(19, 36, 90, 47, QColor("#b98240"))
        p.fillRect(23, 40, 82, 39, QColor("#382e29" if self.occupied else "#f2bb65"))
        if self.occupied:
            if self.peeking:
                p.save()
                p.setClipRect(24, 35, 80, 43)
                p.translate(32, 25)
                draw_character(p, 64, frame=3, state="PEEK", skin=self.settings["pet_palette"], character=self.settings["character"])
                p.restore()
            else:
                p.fillRect(48, 52, 5, 3, QColor("#8decf6"))
                p.fillRect(69, 52, 5, 3, QColor("#8decf6"))
        p.fillRect(19, 66, 90, 17, QColor("#ffc879"))
        p.fillRect(23, 67, 82, 2, QColor("#ffe0a9"))
        p.setPen(QColor(c["text"]))
        p.setFont(QFont("Segoe UI", 9, QFont.DemiBold))
        p.drawText(QRectF(8, 88, 112, 28), Qt.AlignCenter | Qt.TextWordWrap, self.folder["label"][:30])
        p.setFont(QFont("Segoe UI", 8))
        p.setPen(QColor(c["accent"] if self.occupied else c["muted"]))
        p.drawText(QRectF(6, 116, 116, 17), Qt.AlignCenter, "Click to come out" if self.occupied else "Click to hide")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_offset = event.globalPosition().toPoint() - self.pos()
            self.dragged = False

    def mouseMoveEvent(self, event):
        if self.drag_offset is not None and event.buttons() & Qt.LeftButton:
            point = event.globalPosition().toPoint() - self.drag_offset
            if (point - self.pos()).manhattanLength() > 3:
                self.dragged = True
            self.move(clamp_position([point.x(), point.y()], self.width(), self.height(), QApplication.screens()))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.drag_offset is not None:
            self.drag_offset = None
            if self.dragged:
                self.position_changed.emit(self.folder["id"], [self.x(), self.y()])
            elif self.occupied:
                self.emerge_requested.emit()
            else:
                self.jump_requested.emit(self.folder["id"])

    def mouseDoubleClickEvent(self, event):
        self.open_folder()

    def open_folder(self):
        target = self.folder["path"]
        if target:
            if not Path(target).is_dir() or not QDesktopServices.openUrl(QUrl.fromLocalFile(target)):
                self.error.emit("That folder could not be opened. It may have moved.")

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction("Come out" if self.occupied else "Jump inside", lambda: self.emerge_requested.emit() if self.occupied else self.jump_requested.emit(self.folder["id"]))
        if self.folder["path"]:
            menu.addAction("Open folder", self.open_folder)
        menu.addAction("Remove hideout", lambda: self.remove_requested.emit(self.folder["id"]))
        menu.exec(event.globalPos())


class FolderHabitat(QObject):
    error = Signal(str)

    def __init__(self, pet, settings, path, parent=None):
        super().__init__(parent)
        self.pet, self.settings = pet, settings
        self.store = FolderStore(path)
        self.cards = {}
        self.return_timer = QTimer(self)
        self.return_timer.setSingleShot(True)
        self.return_timer.timeout.connect(self.emerge)
        self.peek_timer = QTimer(self)
        self.peek_timer.setInterval(2200)
        self.peek_timer.timeout.connect(self.toggle_peek)
        pet.hop_completed.connect(self.enter)
        self.refresh()

    def refresh(self):
        wanted = {f["id"]: f for f in self.store.folders}
        for identifier in list(self.cards):
            if identifier not in wanted:
                card = self.cards.pop(identifier)
                card.hide()
                card.deleteLater()
        for index, (identifier, folder) in enumerate(wanted.items()):
            if identifier not in self.cards:
                card = FolderCard(folder, self.settings)
                if not folder["position"]:
                    point = clamp_position([card.x() + index * 140, card.y()], card.width(), card.height(), QApplication.screens())
                    card.move(point)
                card.jump_requested.connect(self.jump)
                card.emerge_requested.connect(self.emerge)
                card.position_changed.connect(self.save_position)
                card.remove_requested.connect(self.remove)
                card.error.connect(self.error.emit)
                self.cards[identifier] = card
            self.cards[identifier].configure()
            self.cards[identifier].setVisible(self.settings["folder_play"] and self.settings["portal_toys"])

    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(None, "Choose Jeffery's folder hideout")
        if path:
            self.add_folder(path)

    def add_folder(self, path, position=None):
        try:
            folder = self.store.add(path, position)
            self.refresh()
            self.jump(folder["id"])
            return folder
        except (OSError, ValueError) as exc:
            self.error.emit(str(exc))
            return None

    def jump(self, identifier=None):
        card = self.cards.get(identifier) if identifier else next(iter(self.cards.values()), None)
        if not card:
            return "Add a folder hideout from Jeffery's menu first."
        if not self.settings["folder_play"] or not self.settings["pet_enabled"]:
            return "Enable the buddy and folder hideouts in Buddy Settings first."
        self.emerge(silent=True)
        self.pet.hop_to(card.destination(self.pet), card.folder["id"])
        return "Jumping into " + card.folder["label"] + "."

    def enter(self, identifier):
        card = self.cards.get(identifier)
        if not card:
            return
        self.pet.hide_in(identifier)
        card.occupied, card.peeking = True, False
        card.update()
        self.return_timer.start(self.settings["hide_seconds"] * 1000)
        self.peek_timer.start()

    def toggle_peek(self):
        card = self.cards.get(self.pet.hidden_in)
        if card:
            card.peeking = not card.peeking
            card.update()

    def peek(self):
        card = self.cards.get(self.pet.hidden_in)
        if card:
            card.peeking = True
            card.update()
            return "Peeking out of " + card.folder["label"] + "."
        return "I'm already out here. Try Hide first."

    def emerge(self, silent=False):
        self.return_timer.stop()
        self.peek_timer.stop()
        card = self.cards.get(self.pet.hidden_in)
        if self.pet.hidden_in:
            target = card.destination(self.pet) if card else [self.pet.x(), self.pet.y()]
            self.pet.come_out(target)
            if not silent:
                self.pet.say("Boo! Found me!")
        for portal in self.cards.values():
            portal.occupied = portal.peeking = False
            portal.update()
        return "Back on the desktop."

    def save_position(self, identifier, position):
        try:
            self.store.position(identifier, position)
        except OSError as exc:
            self.error.emit(str(exc))

    def remove(self, identifier):
        try:
            if self.pet.hidden_in == identifier or self.pet.hop_target == identifier:
                self.emerge(silent=True)
                self.pet.cancel_hop()
            self.store.remove(identifier)
            self.refresh()
        except OSError as exc:
            self.error.emit(str(exc))

    def stop(self):
        self.return_timer.stop()
        self.peek_timer.stop()
        for card in self.cards.values():
            card.hide()
