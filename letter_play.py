"""Draggable letter tiles over the desktop; Jeffery and the mouse can push them."""
from __future__ import annotations
from dataclasses import dataclass
import math
import random
import time
from PySide6.QtCore import Qt, QTimer, QRect, QRectF, QPoint
from PySide6.QtGui import QPainter, QColor, QFont, QPen, QRegion, QCursor, QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QWidget, QHBoxLayout, QLineEdit, QPushButton
from notepad_window import notes_style
from themes import palette

WIDTH, HEIGHT = 44, 54


@dataclass
class LetterTile:
    letter: str
    x: float
    y: float
    vx: float = 0
    vy: float = 0

    def step(self, seconds, width, height):
        seconds = max(0, min(0.08, seconds))
        self.x += self.vx * seconds
        self.y += self.vy * seconds
        decay = math.exp(-3 * seconds)
        self.vx *= decay
        self.vy *= decay
        right, bottom = max(0, width - WIDTH), max(68, height - HEIGHT)
        if self.x < 0 or self.x > right:
            self.x = min(max(self.x, 0), right)
            self.vx *= -0.5
        if self.y < 68 or self.y > bottom:
            self.y = min(max(self.y, 68), bottom)
            self.vy *= -0.5

    def impulse(self, x, y, strength=220):
        dx, dy = self.x + WIDTH / 2 - x, self.y + HEIGHT / 2 - y
        distance = math.hypot(dx, dy)
        if distance < 65:
            norm = max(1, distance)
            self.vx = max(-350, min(350, self.vx + (dx / norm if distance else 1) * strength))
            self.vy = max(-350, min(350, self.vy + dy / norm * strength))
            return True
        return False


class LetterPlayground(QWidget):
    def __init__(self, pet, settings):
        super().__init__()
        self.pet, self.settings = pet, settings
        self.tiles, self.drag_index, self.drag_offset = [], None, QPoint()
        self.last_tick, self.last_push = time.monotonic(), 0.0
        self.previous_mouse = QCursor.pos()
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("Jeffery · Letter play")
        self.setMouseTracking(True)
        self.toolbar = QWidget(self)
        self.toolbar.setObjectName("noteCard")
        bar = QHBoxLayout(self.toolbar)
        bar.setContentsMargins(8, 6, 8, 6)
        self.text = QLineEdit("HELLO JEFFERY")
        self.text.setMaxLength(48)
        self.text.setPlaceholderText("Play text")
        self.text.returnPressed.connect(self.line_up)
        bar.addWidget(self.text, 1)
        for label, action in (("Make", self.line_up), ("Scatter", self.scatter), ("Close", self.hide)):
            button = QPushButton(label)
            button.clicked.connect(action)
            bar.addWidget(button)
        self.escape = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self.escape.activated.connect(self.hide)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.configure()

    def configure(self):
        visible = self.isVisible()
        flags = Qt.Tool | Qt.FramelessWindowHint
        if self.settings["always_on_top"]:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.toolbar.setStyleSheet(notes_style(self.settings))
        self.timer.setInterval(66 if self.settings["low_power"] else 33)
        if visible:
            self.show()
            self.last_tick = time.monotonic()
            self.timer.start()
        self.update()

    def open_play(self, text=None):
        screen = QApplication.screenAt(self.pet.geometry().center()) or QApplication.primaryScreen()
        self.setGeometry(screen.availableGeometry())
        self.toolbar.setGeometry(max(4, (self.width() - 560) // 2), 10, min(560, self.width() - 8), 50)
        if text:
            self.text.setText(text[:48])
        self.line_up()
        self.show()
        self.pet.raise_()
        self.last_tick = time.monotonic()
        self.timer.start()
        if self.tiles:
            tile = self.tiles[0]
            target = self.mapToGlobal(QPoint(round(tile.x - self.pet.width() / 2), round(tile.y - self.pet.height() / 2)))
            self.pet.hop_to([target.x(), target.y()])

    def adjust_screen(self):
        if not self.isVisible():
            return
        screen = QApplication.screenAt(self.geometry().center()) or QApplication.primaryScreen()
        geometry = screen.availableGeometry()
        if self.geometry() == geometry:
            return
        old_width, old_height = max(1, self.width()), max(1, self.height())
        self.setGeometry(geometry)
        self.toolbar.setGeometry(max(4, (self.width() - 560) // 2), 10, min(560, self.width() - 8), 50)
        for tile in self.tiles:
            tile.x *= self.width() / old_width
            tile.y *= self.height() / old_height
            tile.step(0, self.width(), self.height())
        self.update_mask()
        self.update()

    def line_up(self):
        letters = [c for c in self.text.text()[:48] if c.isprintable()]
        if not letters:
            letters = list("HELLO")
        columns = max(1, (self.width() - 60) // (WIDTH + 6))
        rows = math.ceil(len(letters) / columns)
        start_y = max(80, min(self.height() - rows * 65 - 20, int(self.height() * 0.55)))
        self.tiles = []
        for index, letter in enumerate(letters):
            if letter.isspace():
                continue
            row, col = divmod(index, columns)
            row_count = min(columns, len(letters) - row * columns)
            start_x = max(4, (self.width() - row_count * (WIDTH + 6)) / 2)
            self.tiles.append(LetterTile(letter, start_x + col * (WIDTH + 6), start_y + row * 65))
        self.drag_index = None
        self.update_mask()
        self.update()

    def scatter(self):
        for tile in self.tiles:
            tile.x = random.uniform(8, max(8, self.width() - WIDTH - 8))
            tile.y = random.uniform(85, max(85, self.height() - HEIGHT - 8))
            tile.vx, tile.vy = random.uniform(-80, 80), random.uniform(-80, 80)
        self.update_mask()
        self.update()

    def update_mask(self):
        # Windows only receives clicks on the visible tiles and toolbar.
        region = QRegion(self.toolbar.geometry())
        for tile in self.tiles:
            region |= QRegion(QRect(round(tile.x) - 2, round(tile.y) - 2, WIDTH + 4, HEIGHT + 4))
        self.setMask(region)

    def tick(self):
        now = time.monotonic()
        elapsed, self.last_tick = now - self.last_tick, now
        mouse = QCursor.pos()
        local_mouse = self.mapFromGlobal(mouse)
        moved = (mouse - self.previous_mouse).manhattanLength() > 2
        self.previous_mouse = mouse
        pet = self.mapFromGlobal(self.pet.geometry().center())
        pushed = False
        for index, tile in enumerate(self.tiles):
            if index == self.drag_index:
                continue
            if moved:
                tile.impulse(local_mouse.x(), local_mouse.y(), 60)
            if self.pet.isVisible() and self.pet.drag_offset is None:
                pushed |= tile.impulse(pet.x(), pet.y(), 75)
            tile.step(elapsed, self.width(), self.height())
        if pushed and now - self.last_push > 1.5:
            self.last_push = now
            self.pet.perform("PUSH", 1.5)
        self.update_mask()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = palette(self.settings)
        colors = ("#a3ead6", "#c7b5f7", "#ffce97", "#9ecff4")
        for index, tile in enumerate(self.tiles):
            p.setPen(QPen(QColor(c["border"]), 1))
            p.setBrush(QColor(colors[index % len(colors)]))
            rect = QRectF(tile.x, tile.y, WIDTH, HEIGHT)
            p.drawRoundedRect(rect, 8, 8)
            p.setPen(QColor("#142333"))
            p.setFont(QFont("Segoe UI", 22, QFont.DemiBold))
            p.drawText(rect, Qt.AlignCenter, tile.letter)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            for index in reversed(range(len(self.tiles))):
                tile = self.tiles[index]
                if QRectF(tile.x, tile.y, WIDTH, HEIGHT).contains(event.position()):
                    self.drag_index = index
                    self.drag_offset = event.position().toPoint() - QPoint(round(tile.x), round(tile.y))
                    tile.vx = tile.vy = 0
                    break

    def mouseMoveEvent(self, event):
        if self.drag_index is not None and event.buttons() & Qt.LeftButton:
            tile = self.tiles[self.drag_index]
            point = event.position().toPoint() - self.drag_offset
            tile.x = min(max(point.x(), 0), self.width() - WIDTH)
            tile.y = min(max(point.y(), 68), self.height() - HEIGHT)
            self.update_mask()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_index = None

    def hideEvent(self, event):
        self.timer.stop()
        self.drag_index = None
        super().hideEvent(event)
