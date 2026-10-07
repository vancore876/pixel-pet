"""A focus session that counts elapsed time rather than timer ticks."""
from __future__ import annotations

import math
import time
from PySide6.QtCore import QObject, QTimer, Signal


class FocusClock:
    def __init__(self, now=time.monotonic):
        self.now = now
        self.deadline = None
        self.saved_seconds = 0.0
        self.state = "idle"

    def start(self, minutes: int):
        self.saved_seconds = minutes * 60.0
        self.deadline = self.now() + self.saved_seconds
        self.state = "running"

    def remaining(self):
        return max(0.0, self.deadline - self.now()) if self.deadline is not None else self.saved_seconds

    def pause(self):
        if self.state == "running":
            self.saved_seconds = self.remaining()
            self.deadline = None
            self.state = "paused"

    def resume(self):
        if self.state == "paused":
            self.deadline = self.now() + self.saved_seconds
            self.state = "running"

    def reset(self):
        self.deadline, self.saved_seconds, self.state = None, 0.0, "idle"

    def tick(self):
        if self.state == "running" and self.remaining() <= 0:
            self.deadline, self.saved_seconds, self.state = None, 0.0, "complete"
            return True
        return False

    def label(self):
        if self.state == "idle":
            return ""
        if self.state == "complete":
            return "Focus complete · Take a break"
        seconds = math.ceil(self.remaining())
        return f"Focus {'paused' if self.state == 'paused' else 'time'} · {seconds // 60:02d}:{seconds % 60:02d}"


class FocusSession(QObject):
    changed = Signal(str)
    completed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.clock = FocusClock()
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.update)

    def start(self, minutes):
        self.clock.start(minutes)
        self.timer.start()
        self.update()

    def toggle_pause(self):
        if self.clock.state == "running":
            self.clock.pause()
            self.timer.stop()
        elif self.clock.state == "paused":
            self.clock.resume()
            self.timer.start()
        self.update()

    def reset(self):
        self.timer.stop()
        self.clock.reset()
        self.changed.emit("")

    def update(self):
        finished = self.clock.tick()
        self.changed.emit(self.clock.label())
        if finished:
            self.timer.stop()
            self.completed.emit()
