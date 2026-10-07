"""Jeffery: three characters, interactions, walking, and parachuting."""
from __future__ import annotations
import random
import time
import math
from pathlib import Path
from PySide6.QtCore import Qt, QTimer, Signal, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QFont, QPixmap, QCursor, QTransform
from PySide6.QtWidgets import QApplication, QWidget
from config import ASSETS, clamp_position
from themes import palette
from alerts import LoadAlerts
from characters import draw_character, ANIMATION_STATES
from motion import ParachuteMotion, HopMotion
from sliding_text import SlidingText, plain_reply


def paint_robot(p, size, frame=0, state="IDLE", direction=1, skin="mint"):
    draw_character(p, size, frame=frame, state=state, direction=direction, skin=skin)


class SpeechBubble(QWidget):
    def __init__(self, pet):
        super().__init__(pet, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.settings = pet.settings
        self.resize(282, 135)
        self.text = ""
        self.lines = SlidingText(parent=self, width=256, max_lines=4, interval=260)
        self.lines.move(13, 27)
        self.lines.grew.connect(lambda: self.resize(282, self.lines.height() + 38))
        self.lines.grew.connect(pet.position_bubble)

    def present(self, text):
        body = plain_reply(text)[:1000]
        self.text = self.settings['pet_name'] + ': ' + body
        self.lines.set_text(body)
        self.show()

    def hideEvent(self, event):
        self.lines.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        c = palette(self.settings)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(c["border"]), 1))
        p.setBrush(QColor(c["panel"]))
        p.drawRoundedRect(QRectF(1, 1, self.width() - 2, self.height() - 9), 12, 12)
        p.setPen(QColor(c["text"]))
        p.setFont(QFont("Segoe UI", 8, QFont.Bold))
        p.drawText(13, 19, self.settings["pet_name"])


class PixelPet(QWidget):
    position_changed = Signal(object)
    menu_requested = Signal(object)
    overlay_requested = Signal()
    hop_completed = Signal(str)
    folder_dropped = Signal(str, object)
    drag_started = Signal()
    drag_released = Signal(object)
    conversation_requested = Signal(str, str)

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAcceptDrops(True)
        self.state, self.direction, self.vertical_direction = "WALK_RIGHT", 1, 0.45
        self.frame, self.paused, self.drag_offset = 1, False, None
        self.was_dragged = False
        self.cpu = self.ram = 0
        self.alerts = LoadAlerts()
        self.last_speech = time.monotonic()
        self.next_state = self.last_speech + 8
        self.last_tick = self.last_speech
        self.action_until, self.action_state = 0.0, None
        self.queued_action, self.drop_motion = None, None
        self.hop_motion, self.hop_target, self.hidden_in = None, "", ""
        self.gaze, self.last_mouse_greeting = (0, 0), 0.0
        self.ai_speech_connected = False
        self.loaded_character, self.sprites = None, {}
        self.bubble = SpeechBubble(self)
        self.bubble_expiry = 0.0
        self.configure()
        rect = QApplication.primaryScreen().availableGeometry()
        position = settings["pet_position"] or [rect.left() + 120, rect.bottom() - self.height() + 1]
        self.move(clamp_position(position, self.width(), self.height(), QApplication.screens()))
        self.real_x, self.real_y = float(self.x()), float(self.y())
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.animate)
        self.timer.start(200 if settings["low_power"] else 100)

    def load_sprites(self):
        base = ASSETS / self.settings["character"]
        if not base.is_dir() and self.settings["character"] == "robot":
            base = ASSETS
        result = {}
        for state in ANIMATION_STATES:
            prefix = "walk" if state.startswith("WALK") else state.lower()
            frames = [QPixmap(str(path)) for path in sorted(base.glob(prefix + "_*.png"))]
            result[state] = [p for p in frames if not p.isNull()]
        return result

    def configure(self):
        visible = self.isVisible()
        flags = Qt.Tool | Qt.FramelessWindowHint
        if self.settings["always_on_top"]:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.bubble.setWindowFlag(Qt.WindowStaysOnTopHint, self.settings["always_on_top"])
        self.bubble.lines.setStyleSheet("color: " + palette(self.settings)["text"] + ";")
        size = self.settings["pet_size"]
        self.resize(size, size + int(size * 0.6) if self.drop_motion else size)
        self.setWindowTitle(f"PixelSystem Buddy · {self.settings['pet_name']}")
        if self.loaded_character != self.settings["character"]:
            self.sprites = self.load_sprites()
            self.loaded_character = self.settings["character"]
        if hasattr(self, "timer"):
            self.timer.setInterval(200 if self.settings["low_power"] else 100)
        if visible:
            self.show()
        if not self.settings["speech"] or self.settings["quiet_mode"]:
            self.bubble.hide()
        self.update()

    def monitor_rect(self):
        if self.settings["follow_active_monitor"]:
            screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        elif self.drop_motion or self.settings["roaming_mode"] == "free":
            screen = QApplication.screenAt(self.geometry().center()) or QApplication.primaryScreen()
        else:
            screen = QApplication.primaryScreen()
        return screen.availableGeometry()

    def accept_snapshot(self, snapshot):
        self.cpu, self.ram = snapshot.cpu or 0, snapshot.memory_percent or 0
        now = time.monotonic()
        notify = self.settings["reactions"] and self.settings["speech"] and self.isVisible() and not self.settings["quiet_mode"]
        message = self.alerts.evaluate(snapshot.cpu, snapshot.memory_percent, self.settings, now, notify and now - self.last_speech >= 20)
        if message:
            self.say(message)

    def say(self, text):
        if not self.settings["speech"] or not self.isVisible() or self.settings["quiet_mode"]:
            return
        self.bubble.present(text)
        self.bubble_expiry = time.monotonic() + 7 + len(self.bubble.lines.lines) * 0.26
        self.last_speech = time.monotonic()
        self.position_bubble()
        self.bubble.show()
        self.bubble.update()

    def position_bubble(self):
        position = [self.x() + self.width() // 2 - self.bubble.width() // 2, self.y() - self.bubble.height()]
        self.bubble.move(clamp_position(position, self.bubble.width(), self.bubble.height(), QApplication.screens()))

    def perform(self, state, seconds=3):
        if state not in ANIMATION_STATES:
            return
        if self.hidden_in:
            return
        if self.drop_motion or self.hop_motion:
            self.queued_action = (state, seconds)
            return
        self.action_state, self.state = state, state
        self.action_until = time.monotonic() + seconds
        self.frame = 0
        self.timer.setInterval(200 if self.settings["low_power"] else 100)
        self.update()

    def cancel_hop(self):
        self.hop_motion, self.hop_target = None, ""
        self.real_x, self.real_y = float(self.x()), float(self.y())

    def hop_to(self, position, identifier=""):
        self.cancel_drop()
        self.cancel_hop()
        self.queued_action = None
        target = clamp_position(position, self.width(), self.height(), QApplication.screens())
        self.hop_motion = HopMotion(self.x(), self.y(), target.x(), target.y())
        self.hop_target, self.state = identifier, "HOP"
        self.last_tick = time.monotonic()
        self.timer.setInterval(160 if self.settings["low_power"] else 80)
        self.update()

    def hide_in(self, identifier):
        self.cancel_drop()
        self.cancel_hop()
        self.hidden_in = identifier
        self.bubble.hide()
        self.hide()

    def come_out(self, position):
        self.hidden_in = ""
        point = clamp_position(position, self.width(), self.height(), QApplication.screens())
        self.move(point)
        self.real_x, self.real_y = float(self.x()), float(self.y())
        if self.settings["pet_enabled"]:
            self.show()
        self.perform("PEEK", 2)

    def interact(self, action):
        messages = {"PET": "That's nice!", "EAT": "Snack time!", "WAVE": "Hey boss!", "DANCE": "Tiny dance break!", "JUMP": "Here we go!", "SLEEP": "A tiny nap…"}
        self.perform(action, 8 if action == "SLEEP" else 3)
        if self.ai_speech_connected:
            self.conversation_requested.emit('interaction', action)
        else:
            self.say(messages.get(action, "I'm here!"))

    def cancel_drop(self):
        if self.drop_motion:
            feet = self.y() + self.height()
            self.drop_motion = None
            size = self.settings["pet_size"]
            self.resize(size, size)
            self.move(self.x(), feet - size)
        self.action_state = None
        self.real_x, self.real_y = float(self.x()), float(self.y())

    def begin_drop(self):
        rect = self.monitor_rect()
        if not self.settings["parachute"] or self.y() + self.height() >= rect.bottom() - 2:
            self.position_changed.emit([self.x(), self.y()])
            return False
        feet = self.y() + self.height()
        size = self.settings["pet_size"]
        self.resize(size, size + int(size * 0.6))
        self.move(clamp_position([self.x(), feet - self.height()], self.width(), self.height(), QApplication.screens()))
        self.real_x, self.real_y = float(self.x()), float(self.y())
        self.drop_motion = ParachuteMotion(self.real_y, max(rect.top(), rect.bottom() - self.height() + 1))
        self.action_state, self.state = None, "PARACHUTE"
        self.frame, self.last_tick = 0, time.monotonic()
        self.timer.setInterval(160 if self.settings["low_power"] else 80)
        self.update()
        return True

    def advance_drop(self, elapsed):
        rect = self.monitor_rect()
        self.drop_motion.ground = max(rect.top(), rect.bottom() - self.height() + 1)
        landed = self.drop_motion.step(elapsed)
        self.real_y = self.drop_motion.y
        self.real_x = min(max(self.real_x, rect.left()), rect.right() - self.width() + 1)
        self.move(round(self.real_x), round(self.real_y))
        if landed:
            self.drop_motion = None
            size = self.settings["pet_size"]
            self.resize(size, size)
            self.move(round(self.real_x), rect.bottom() + 1 - size)
            self.real_y = float(self.y())
            self.perform("LANDING", 0.7)
            self.position_changed.emit([self.x(), self.y()])

    def animate(self):
        now = time.monotonic()
        elapsed = min(0.25, now - self.last_tick)
        self.last_tick = now
        if now >= self.bubble_expiry:
            self.bubble.hide()
        if not self.isVisible():
            self.timer.setInterval(1000)
            return
        self.frame += 1
        if self.hop_motion:
            x, y, finished = self.hop_motion.step(elapsed)
            point = clamp_position([round(x), round(y)], self.width(), self.height(), QApplication.screens())
            self.move(point)
            self.real_x, self.real_y = float(self.x()), float(self.y())
            self.state = "HOP"
            self.position_bubble()
            self.update()
            if finished:
                destination = self.hop_target
                self.cancel_hop()
                self.perform("LANDING", 0.7)
                self.hop_completed.emit(destination)
            return
        if self.drag_offset is not None:
            self.state = "DRAG"
            self.update()
            return
        if self.drop_motion:
            self.advance_drop(elapsed)
            self.position_bubble()
            self.update()
            return
        if self.action_state is not None:
            if now < self.action_until:
                self.state = self.action_state
                self.update()
                return
            self.action_state, self.state, self.next_state = None, "IDLE", 0
            if self.queued_action:
                state, seconds = self.queued_action
                self.queued_action = None
                self.perform(state, seconds)
                return
        if self.paused:
            self.timer.setInterval(1000)
            self.update()
            return
        rect = self.monitor_rect()
        if self.settings["reactions"] and self.cpu > 70:
            self.state = "EXCITED"
        elif now >= self.next_state or self.state == "EXCITED":
            tricks = ["WAVE", "YAWN", "STRETCH", "SPIN", "FLIP", "ROLL", "SNEEZE", "SCARED", "LAUGH", "SIT", "BALANCE", "TIPTOE", "RUN", "CLIMB", "SLIDE", "SKATE", "BOUNCE", "MAGIC", "UMBRELLA", "JUGGLE", "GROOM", "SALUTE", "FACEPALM", "LEAN", "SNEAK"]
            choices, weights = (["WALK", "IDLE", "SLEEP"] + tricks, [7, 3, 0.5] + [0.45] * len(tricks)) if self.settings["playful"] else (["WALK", "IDLE", "SLEEP"], [7, 3, 1])
            self.state = random.choices(choices, weights)[0]
            self.direction, self.vertical_direction = random.choice([-1, 1]), random.uniform(-0.7, 0.7)
            if self.state == "WALK":
                self.state = "WALK_RIGHT" if self.direction > 0 else "WALK_LEFT"
            self.next_state = now + (random.uniform(2.5, 5) if self.state in tricks else random.uniform(4, 9))
        if self.settings["follow_mouse"]:
            cursor = QCursor.pos()
            dx = cursor.x() - self.width() / 2 - self.real_x
            dy = cursor.y() - self.height() / 2 - self.real_y if self.settings["roaming_mode"] == "free" else 0
            if abs(dx) > 6 or abs(dy) > 6:
                self.direction = 1 if dx >= 0 else -1
                self.vertical_direction = dy / max(6, abs(dx), abs(dy))
                self.state = "WALK_RIGHT" if self.direction > 0 else "WALK_LEFT"
            else:
                self.state = "IDLE"
        cursor = QCursor.pos()
        dx = cursor.x() - self.width() / 2 - self.real_x
        dy = cursor.y() - self.height() / 2 - self.real_y
        distance = math.hypot(dx, dy)
        mouse_mode = self.settings["mouse_mode"]
        self.gaze = (int(math.copysign(1, dx)) if abs(dx) > 20 else 0,
                     int(math.copysign(1, dy)) if abs(dy) > 20 else 0) if mouse_mode != "off" else (0, 0)
        direct_motion = False
        if mouse_mode in ("chase", "shy") and (mouse_mode == "chase" or distance < 220):
            sign = -1 if mouse_mode == "shy" else 1
            if distance > 36 or sign == -1:
                speed = self.settings["speed"] * 2.5
                self.real_x += sign * dx / max(1, distance) * speed * elapsed
                self.real_y += sign * dy / max(1, distance) * speed * elapsed
                self.direction = 1 if sign * dx >= 0 else -1
                self.state = "SHY" if sign == -1 else "CHASE"
            else:
                self.state = "LOOK"
            direct_motion = True
        elif mouse_mode == "watch" and distance < 190 and not self.settings["follow_mouse"] and (self.state.startswith('WALK') or self.state in ('IDLE', 'SLEEP', 'LOOK', 'CHASE', 'SHY')):
            self.state = "LOOK"
            if now - self.last_mouse_greeting > 60:
                self.last_mouse_greeting = now
                if self.ai_speech_connected:
                    self.conversation_requested.emit('mouse_greeting', '')
                else:
                    self.say("I see your mouse! Click me for a pat.")
        if not direct_motion and (self.state.startswith("WALK") or self.state in ("EXCITED", "RUN", "SKATE", "TIPTOE", "SNEAK")):
            multiplier = 1.5 if self.settings["reactions"] and self.cpu >= 40 else 1
            multiplier *= 2 if self.state in ("RUN", "SKATE") else 0.55 if self.state in ("TIPTOE", "SNEAK") else 1
            self.real_x += self.direction * self.settings["speed"] * multiplier * elapsed
            if self.settings["roaming_mode"] == "free":
                self.real_y += self.vertical_direction * self.settings["speed"] * multiplier * elapsed
        low, high = rect.left(), max(rect.left(), rect.right() - self.width() + 1)
        if self.real_x <= low:
            self.real_x, self.direction = float(low), 1
        elif self.real_x >= high:
            self.real_x, self.direction = float(high), -1
        if self.state.startswith("WALK"):
            self.state = "WALK_RIGHT" if self.direction > 0 else "WALK_LEFT"
        bottom = max(rect.top(), rect.bottom() - self.height() + 1)
        if self.settings["roaming_mode"] == "free" or direct_motion:
            if self.real_y <= rect.top():
                self.real_y, self.vertical_direction = float(rect.top()), abs(self.vertical_direction)
            elif self.real_y >= bottom:
                self.real_y, self.vertical_direction = float(bottom), -abs(self.vertical_direction)
        else:
            self.real_y = float(bottom)
        self.move(round(self.real_x), round(self.real_y))
        if self.bubble.isVisible():
            self.position_bubble()
        if self.settings["speech"] and not self.settings["quiet_mode"] and now - self.last_speech > self.settings["speech_frequency"]:
            if self.ai_speech_connected:
                self.last_speech = now
                self.conversation_requested.emit('check_in', '')
            else:
                self.say(random.choice(["I'm still here.", "Taking a tiny walk.", "Your notes are safe with me."]))
        interval = 200 if self.settings["low_power"] else 100
        self.timer.setInterval(max(interval, 300) if self.state in ("IDLE", "SLEEP") else interval)
        self.update()

    def set_paused(self, paused):
        self.paused = paused
        if paused and not self.drop_motion:
            self.action_state, self.state = None, "IDLE"
            self.timer.setInterval(1000)
        elif not paused:
            self.real_x, self.real_y = float(self.x()), float(self.y())
            self.next_state, self.last_tick = 0, time.monotonic()
            self.timer.setInterval(200 if self.settings["low_power"] else 100)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)
        frames = self.sprites.get(self.state, []) if self.state != "PARACHUTE" else []
        if frames:
            image = frames[self.frame % len(frames)]
            if self.direction < 0:
                image = image.transformed(QTransform().scale(-1, 1))
            target = image.size().scaled(self.size(), Qt.KeepAspectRatio)
            p.drawPixmap((self.width() - target.width()) // 2, self.height() - target.height(), image.scaled(target, Qt.KeepAspectRatio, Qt.FastTransformation))
        else:
            gaze = (self.gaze[0] * self.direction, self.gaze[1])
            draw_character(p, self.width(), self.height(), self.frame, self.state, self.direction, self.settings["pet_palette"], self.settings["character"], gaze)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_started.emit()
            self.cancel_hop()
            self.cancel_drop()
            self.drag_offset = event.globalPosition().toPoint() - self.pos()
            self.was_dragged, self.state = False, "DRAG"
            self.timer.setInterval(100)
            self.update()

    def mouseMoveEvent(self, event):
        if self.drag_offset is not None and event.buttons() & Qt.LeftButton:
            point = event.globalPosition().toPoint() - self.drag_offset
            if (point - self.pos()).manhattanLength() > 3:
                self.was_dragged = True
            self.move(clamp_position([point.x(), point.y()], self.width(), self.height(), QApplication.screens()))
            self.position_bubble()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.drag_offset is not None:
            self.real_x, self.real_y = float(self.x()), float(self.y())
            self.drag_offset = None
            if self.was_dragged:
                self.state = "IDLE"
                self.drag_released.emit([self.x() + self.width() // 2, self.y() + self.height() // 2])
                if not self.hop_motion:
                    self.begin_drop()
            else:
                self.interact("PET")

    def mouseDoubleClickEvent(self, event):
        self.overlay_requested.emit()

    def contextMenuEvent(self, event):
        self.menu_requested.emit(event.globalPos())

    def dragEnterEvent(self, event):
        if any(Path(url.toLocalFile()).is_dir() for url in event.mimeData().urls() if url.isLocalFile()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        folder = next((url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile() and Path(url.toLocalFile()).is_dir()), None)
        if folder:
            self.folder_dropped.emit(folder, [self.x() + self.width(), self.y() - 100])
            event.acceptProposedAction()

    def hideEvent(self, event):
        self.bubble.hide()
        super().hideEvent(event)

    def showEvent(self, event):
        if hasattr(self, "timer"):
            self.last_tick = time.monotonic()
            self.timer.setInterval(1000 if self.paused and not self.action_state else 200 if self.settings["low_power"] else 100)
        super().showEvent(event)

    def closeEvent(self, event):
        event.accept()
        self.hide()
