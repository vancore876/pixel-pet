"""Plain text revealed as individually sliding lines, without Markdown clutter."""
from __future__ import annotations
import html
import re
from PySide6.QtCore import Qt, QTimer, QPoint, QPropertyAnimation, QEasingCurve, Signal
from PySide6.QtGui import QFont, QTextLayout
from PySide6.QtWidgets import QWidget, QLabel, QScrollArea, QVBoxLayout, QGraphicsOpacityEffect


def plain_reply(text):
    text = str(text).replace("\r\n", "\n")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</?(?:b|strong|em|i|p|div|span)[^>]*>", "", text, flags=re.I)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r"\1 (\2)", text)
    text = re.sub(r"(?m)^\s*#{1,6}\s+", "", text)
    text = re.sub(r'(?<!\w)\*\*(\S(?:.*?\S)?)\*\*(?!\w)', r'\1', text)
    text = text.replace("`", "")
    lines = []
    for line in text.splitlines():
        if re.fullmatch(r"\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*", line):
            continue
        if line.strip().startswith("|"):
            line = " · ".join(part.strip() for part in line.strip().strip("|").split("|") if part.strip())
        lines.append(line.rstrip())
    return html.unescape("\n".join(lines)).strip()


def wrap_lines(text, font, width):
    result = []
    for paragraph in text.split("\n"):
        if not paragraph:
            result.append("")
            continue
        layout = QTextLayout(paragraph, font)
        layout.beginLayout()
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(max(30, width))
            result.append(paragraph[line.textStart():line.textStart() + line.textLength()].rstrip())
        layout.endLayout()
    return result


class SlidingText(QWidget):
    finished = Signal()
    grew = Signal()

    def __init__(self, text="", parent=None, width=420, max_lines=0, interval=230):
        super().__init__(parent)
        self.setFont(QFont("Segoe UI", 10))
        self.line_height = 24
        self.max_lines, self.interval = max_lines, interval
        self.lines, self.labels, self.animations = [], [], []
        self.cursor = 0
        self.text = ""
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.reveal_next)
        self.setFixedWidth(width)
        self.set_text(text)

    def set_text(self, text):
        self.timer.stop()
        for animation in self.animations:
            animation.stop()
            animation.deleteLater()
        self.animations = []
        for label in self.labels:
            label.deleteLater()
        self.labels = []
        self.text = plain_reply(text)
        self.lines = wrap_lines(self.text, self.font(), self.width() - 4)
        self.cursor = 0
        self.setFixedHeight(self.line_height)
        if self.lines:
            self.reveal_next()
            if self.cursor < len(self.lines):
                self.timer.start(self.interval)

    def reveal_next(self, animated=True):
        if self.cursor >= len(self.lines):
            self.timer.stop()
            self.finished.emit()
            return
        if self.max_lines and len(self.labels) >= self.max_lines:
            first = self.labels.pop(0)
            first.hide()
            first.deleteLater()
            for index, old in enumerate(self.labels):
                old.move(0, index * self.line_height)
        label = QLabel(self.lines[self.cursor], self)
        label.setTextFormat(Qt.PlainText)
        label.setFont(self.font())
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.resize(self.width(), self.line_height)
        y = len(self.labels) * self.line_height
        self.labels.append(label)
        self.cursor += 1
        self.setFixedHeight(max(1, len(self.labels)) * self.line_height)
        if animated:
            effect = QGraphicsOpacityEffect(label)
            label.setGraphicsEffect(effect)
            for target, prop, start, end in ((label, b"pos", QPoint(0, y - 12), QPoint(0, y)),
                                             (effect, b"opacity", 0.0, 1.0)):
                animation = QPropertyAnimation(target, prop, self)
                animation.setDuration(190)
                animation.setEasingCurve(QEasingCurve.OutCubic)
                animation.setStartValue(start)
                animation.setEndValue(end)
                self.animations.append(animation)
                animation.finished.connect(lambda a=animation: self.release_animation(a))
                animation.start()
        else:
            label.move(0, y)
        label.show()
        self.grew.emit()
        if self.cursor >= len(self.lines):
            self.timer.stop()
            self.finished.emit()

    def release_animation(self, animation):
        if animation in self.animations:
            self.animations.remove(animation)
        animation.deleteLater()

    def stop(self):
        self.timer.stop()

    def reflow(self, width):
        if self.width() == width:
            return
        progress = self.cursor / max(1, len(self.lines))
        blocked = self.blockSignals(True)
        self.setFixedWidth(width)
        self.set_text(self.text)
        count = max(1, round(len(self.lines) * progress))
        self.timer.stop()
        while self.cursor < count:
            self.reveal_next(animated=False)
        if self.cursor < len(self.lines):
            self.timer.start(self.interval)
        self.blockSignals(blocked)
        self.grew.emit()


class SlideTranscript(QScrollArea):
    """Message cards; appendPlainText/toPlainText retain the old test interface."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setObjectName("transcript")
        self.page = QWidget()
        self.layout = QVBoxLayout(self.page)
        self.layout.setContentsMargins(14, 14, 14, 14)
        self.layout.setSpacing(18)
        self.layout.addStretch(1)
        self.setWidget(self.page)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.messages = []
        self.cards = []
        self.bodies = []
        self.reflow_timer = QTimer(self)
        self.reflow_timer.setSingleShot(True)
        self.reflow_timer.timeout.connect(self.reflow)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'reflow_timer'):
            self.reflow_timer.start(80)

    def reflow(self):
        for body in self.bodies:
            body.reflow(max(280, self.viewport().width() - 44))

    def appendPlainText(self, value):
        sender, marker, text = value.partition(": ")
        if not marker:
            sender, text = "", value
        self.add_message(sender, text)

    def add_message(self, sender, text):
        self.messages.append(f"{sender}: {plain_reply(text)}" if sender else plain_reply(text))
        card = QWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        title = QLabel(sender)
        title.setTextFormat(Qt.PlainText)
        title.setStyleSheet("color: #91cfbb; font-weight: 600; font-size: 12px;")
        layout.addWidget(title)
        body = SlidingText(text, width=max(280, self.viewport().width() - 44))
        body.grew.connect(self.scroll_if_reading_latest)
        layout.addWidget(body)
        self.layout.insertWidget(self.layout.count() - 1, card)
        self.cards.append(card)
        self.bodies.append(body)
        while len(self.cards) > 40:
            self.messages.pop(0)
            self.bodies.pop(0).stop()
            self.cards.pop(0).deleteLater()
        QTimer.singleShot(0, self.scroll_if_reading_latest)

    def scroll_if_reading_latest(self):
        bar = self.verticalScrollBar()
        if bar.maximum() - bar.value() < 100:
            QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))

    def toPlainText(self):
        return "\n\n".join(self.messages)

    def clear(self):
        for body in self.bodies:
            body.stop()
        for card in self.cards:
            card.deleteLater()
        self.messages, self.cards, self.bodies = [], [], []

    def stop(self):
        self.reflow_timer.stop()
        for body in self.bodies:
            body.stop()
