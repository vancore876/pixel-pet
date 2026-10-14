"""A passive employee message card with recipient-specific reminder actions."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPlainTextEdit, QPushButton, QComboBox)

from config import clamp_position
from ui_style import ui_palette, window_style


def team_style(settings):
    colors = ui_palette(settings)
    return window_style(settings) + f"""
        QWidget#teamCard, QWidget#teamPage {{ background: {colors['bg']}; }}
        QSplitter::handle {{ background: {colors['bg']}; width: 10px; }}
    """


def plain_label(text=""):
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    return label


class TeamPopup(QWidget):
    """Presentation only: dismissing a card never acknowledges its message."""
    open_requested = Signal(str)
    acknowledge_requested = Signal(str)
    snooze_requested = Signal(str, int)

    def __init__(self, settings):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.settings = settings
        self.identifier = ""
        self.notification = {}
        self.setObjectName("teamCard")
        self.setWindowTitle("Jeffery · Team message")
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)
        self.resize(440, 335)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        header = QHBoxLayout()
        self.heading = plain_label("New team message")
        self.heading.setStyleSheet("font-size: 15px; font-weight: 600;")
        header.addWidget(self.heading, 1)
        self.dismiss_button = QPushButton("×")
        self.dismiss_button.setAccessibleName("Dismiss this popup; keep its reminders")
        self.dismiss_button.setToolTip("Close the popup. Scheduled reminders continue.")
        self.dismiss_button.setFixedWidth(32)
        self.dismiss_button.clicked.connect(self.dismiss)
        header.addWidget(self.dismiss_button)
        layout.addLayout(header)
        self.title = plain_label()
        self.title.setStyleSheet("font-size: 14px; font-weight: 600;")
        layout.addWidget(self.title)
        self.details = plain_label()
        self.details.setObjectName("hint")
        layout.addWidget(self.details)
        self.body = QPlainTextEdit()
        self.body.setReadOnly(True)
        layout.addWidget(self.body, 1)
        actions = QHBoxLayout()
        self.snooze_minutes = QComboBox()
        for minutes in (5, 15, 30, 60):
            self.snooze_minutes.addItem(f"{minutes} min", minutes)
        self.snooze_minutes.setAccessibleName("Snooze duration")
        actions.addWidget(self.snooze_minutes)
        self.snooze_button = QPushButton("Later")
        self.snooze_button.clicked.connect(self.snooze)
        actions.addWidget(self.snooze_button)
        self.open_button = QPushButton("Open")
        self.open_button.clicked.connect(lambda: self.act(self.open_requested))
        actions.addWidget(self.open_button)
        self.acknowledge_button = QPushButton("Acknowledge")
        self.acknowledge_button.setToolTip("Stop your reminders for this message. Other recipients keep theirs.")
        self.acknowledge_button.clicked.connect(lambda: self.act(self.acknowledge_requested))
        actions.addWidget(self.acknowledge_button)
        layout.addLayout(actions)
        self.hint = plain_label("Reminders are yours. Acknowledging a message does not complete its source note.")
        self.hint.setObjectName("hint")
        layout.addWidget(self.hint)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.dismiss)
        self.configure()

    def configure(self):
        visible = self.isVisible()
        self.setStyleSheet(team_style(self.settings))
        self.setWindowFlag(Qt.WindowStaysOnTopHint, self.settings["always_on_top"])
        if self.settings["quiet_mode"]:
            self.dismiss()
        elif visible:
            self.show()

    def present(self, notification, anchor=None):
        if self.settings["quiet_mode"]:
            return False
        self.notification = dict(notification)
        self.identifier = str(notification["id"])
        kind = notification.get("notification_kind", notification.get("kind", "received"))
        subject = "Shared note" if notification.get("entity_kind") == "note" else "Team message"
        self.heading.setText(subject + " reminder" if kind == "reminder" else "New " + subject.lower())
        self.title.setText(str(notification.get("title", "Team message")))
        sender = notification.get("author_name", notification.get("sender_name", notification.get("sender", "")))
        customer = notification.get("customer", "")
        detail = []
        if sender:
            detail.append("From: " + str(sender))
        if customer:
            detail.append("Customer: " + str(customer))
        if notification.get("order_ref"):
            detail.append("Order: " + str(notification["order_ref"]))
        if notification.get("order_status"):
            detail.append("Status: " + str(notification["order_status"]).title())
        self.details.setText(" · ".join(detail))
        self.details.setVisible(bool(detail))
        body = str(notification.get("body", ""))
        checklist = notification.get("checklist", [])
        if checklist:
            body += "\n\n" + "\n".join(f"[{'✓' if item.get('done') else ' '}] {item.get('quantity', 1)} × {item.get('text', '')}"
                for item in checklist[:100] if isinstance(item, dict))
        self.body.setPlainText(body.strip())
        screen = QApplication.screenAt(anchor) if anchor is not None else None
        screen = screen or QApplication.primaryScreen()
        if screen is not None:
            rect = screen.availableGeometry()
            self.move(clamp_position([rect.right() - self.width() - 20,
                rect.bottom() - self.height() - 20], self.width(), self.height(), QApplication.screens()))
        self.show()
        self.timer.start(20000)
        return True

    def act(self, signal):
        if self.identifier:
            signal.emit(self.identifier)
        self.dismiss()

    def snooze(self):
        if self.identifier:
            self.snooze_requested.emit(self.identifier, int(self.snooze_minutes.currentData()))
        self.dismiss()

    def dismiss(self):
        self.timer.stop()
        self.hide()

    def dismiss_if_inactive(self, active_message_ids):
        if self.identifier and self.identifier not in active_message_ids:
            self.dismiss()

    def enterEvent(self, event):
        self.timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self.isVisible():
            self.timer.start(15000)
        super().leaveEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def shutdown(self):
        self.dismiss()
