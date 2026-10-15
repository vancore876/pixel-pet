"""A passive employee message card with recipient-specific reminder actions."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal, QPoint, QPointF
from PySide6.QtGui import QPainter, QTextLayout, QTextOption
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPlainTextEdit, QPushButton, QComboBox, QToolButton, QMenu, QSizePolicy,
    QLayout)

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


class _ElidingLabel(QLabel):
    """Keep the literal full label available while bounding the visible line."""

    def __init__(self, text=""):
        super().__init__(text)
        self.setTextFormat(Qt.PlainText)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setMinimumWidth(0)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(self.palette().windowText().color())
        text = self.fontMetrics().elidedText(self.text(), Qt.ElideRight, self.width())
        painter.drawText(self.rect(), Qt.AlignLeft | Qt.AlignVCenter, text)


class _PreviewLabel(_ElidingLabel):
    """Render at most two plain-text lines, with an ellipsis on overflow."""

    def paintEvent(self, event):
        text = " ".join(self.text().split())[:4096]
        if not text or self.width() <= 0:
            return
        painter = QPainter(self)
        painter.setPen(self.palette().windowText().color())
        layout = QTextLayout(text, self.font())
        option = layout.textOption()
        option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        layout.beginLayout()
        first = layout.createLine()
        first.setLineWidth(self.width())
        first.setPosition(QPointF(0, 0))
        length = first.textLength()
        layout.endLayout()
        first.draw(painter, QPointF(0, 0))
        rest = text[length:].lstrip()
        if rest:
            final = self.fontMetrics().elidedText(rest, Qt.ElideRight, self.width())
            painter.drawText(0, self.fontMetrics().height() + self.fontMetrics().ascent(), final)


class TeamPopup(QWidget):
    """Presentation only: dismissing a card never acknowledges its message."""
    open_requested = Signal(str)
    acknowledge_requested = Signal(str)
    snooze_requested = Signal(str, int)

    def __init__(self, settings):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint
                         | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.settings = settings
        self.identifier = ""
        self.notification = {}
        self.expanded = False
        self._anchor = None
        self.setObjectName("teamCard")
        self.setWindowTitle("Jeffery · Team message")
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)
        # The scrolling body can shrink on small/high-DPI displays. Hidden
        # expanded controls must never establish a large minimum card size.
        layout.setSizeConstraint(QLayout.SetNoConstraint)
        header = QHBoxLayout()
        header.setSpacing(4)
        self.heading = _ElidingLabel("New team message")
        self.heading.setStyleSheet("font-size: 13px; font-weight: 600;")
        header.addWidget(self.heading, 1)
        self.expand_button = QToolButton()
        self.expand_button.setCheckable(True)
        self.expand_button.setFixedSize(28, 28)
        self.expand_button.clicked.connect(self.set_expanded)
        header.addWidget(self.expand_button)
        self.dismiss_button = QPushButton("×")
        self.dismiss_button.setAccessibleName("Dismiss this popup; keep its reminders")
        self.dismiss_button.setToolTip("Close the popup. Scheduled reminders continue.")
        self.dismiss_button.setFixedSize(28, 28)
        self.dismiss_button.clicked.connect(self.dismiss)
        header.addWidget(self.dismiss_button)
        layout.addLayout(header)
        self.title = _ElidingLabel()
        self.title.setStyleSheet("font-size: 13px; font-weight: 600;")
        layout.addWidget(self.title)
        self.details = _ElidingLabel()
        self.details.setObjectName("hint")
        self.details.setStyleSheet("font-size: 12px;")
        layout.addWidget(self.details)
        self.preview = _PreviewLabel()
        self.preview.setToolTip("Expand to read the full message.")
        layout.addWidget(self.preview)
        self.body = QPlainTextEdit()
        self.body.setReadOnly(True)
        self.body.setMinimumSize(0, 0)
        layout.addWidget(self.body, 1)
        self.expanded_actions = QWidget()
        actions = QHBoxLayout(self.expanded_actions)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(4)
        self.snooze_minutes = QComboBox()
        for minutes in (5, 15, 30, 60):
            self.snooze_minutes.addItem(f"{minutes} min", minutes)
        self.snooze_minutes.setAccessibleName("Snooze duration")
        actions.addWidget(self.snooze_minutes, 1)
        self.snooze_button = QPushButton("Later")
        self.snooze_button.clicked.connect(self.snooze)
        actions.addWidget(self.snooze_button)
        self.acknowledge_button = QPushButton("Acknowledge")
        self.acknowledge_button.setToolTip("Stop your reminders for this message. Other recipients keep theirs.")
        self.acknowledge_button.clicked.connect(lambda: self.act(self.acknowledge_requested))
        actions.addWidget(self.acknowledge_button)
        layout.addWidget(self.expanded_actions)
        self.hint = plain_label("Reminders are yours. Acknowledging a message does not complete its source note.")
        self.hint.setObjectName("hint")
        layout.addWidget(self.hint)
        footer = QHBoxLayout()
        self.open_button = QPushButton("Open")
        self.open_button.setObjectName("primary")
        self.open_button.clicked.connect(lambda: self.act(self.open_requested))
        footer.addWidget(self.open_button)
        footer.addStretch(1)
        self.more_button = QToolButton()
        self.more_button.setText("More")
        self.more_button.setPopupMode(QToolButton.InstantPopup)
        self.more_button.setAccessibleName("More notification actions")
        self.more_menu = QMenu(self)
        for minutes in (5, 15, 30, 60):
            action = self.more_menu.addAction(f"Remind me in {minutes} minutes")
            action.triggered.connect(lambda checked=False, value=minutes: self.snooze_for(value))
        self.more_menu.addSeparator()
        self.acknowledge_action = self.more_menu.addAction("Acknowledge · stop my reminders")
        self.acknowledge_action.triggered.connect(lambda: self.act(self.acknowledge_requested))
        self.more_button.setMenu(self.more_menu)
        footer.addWidget(self.more_button)
        layout.addLayout(footer)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.dismiss)
        self.more_menu.aboutToShow.connect(self.timer.stop)
        self.more_menu.aboutToHide.connect(self._resume_timer)
        self.configure()

    def configure(self):
        visible = self.isVisible()
        self.setStyleSheet(team_style(self.settings) + """
            QWidget#teamCard QPushButton, QWidget#teamCard QToolButton {
                padding: 4px 8px; min-height: 18px;
            }
            QWidget#teamCard QComboBox { padding: 4px 7px; min-height: 18px; }
        """)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, self.settings["always_on_top"])
        self.set_expanded(self._default_expanded(), restart_timer=False)
        if self.settings["quiet_mode"]:
            self.dismiss()
        elif visible:
            self.show()
            self._resume_timer()

    def _default_expanded(self):
        try:
            return self.settings["notification_size"] == "expanded"
        except (KeyError, TypeError):
            return False

    def set_expanded(self, expanded, *, restart_timer=True):
        """Changing the view is presentation only; receipt state is untouched."""
        self.expanded = bool(expanded)
        self.expand_button.setChecked(self.expanded)
        self.expand_button.setArrowType(Qt.UpArrow if self.expanded else Qt.DownArrow)
        action = "Collapse" if self.expanded else "Expand"
        self.expand_button.setAccessibleName(action + " notification")
        self.expand_button.setToolTip(action + " message")
        self.preview.setVisible(not self.expanded)
        self.body.setVisible(self.expanded)
        self.expanded_actions.setVisible(self.expanded)
        self.hint.setVisible(self.expanded)
        self.more_button.setVisible(not self.expanded)
        self.title.setFixedHeight(self.title.fontMetrics().height() + 2)
        self.details.setFixedHeight(self.details.fontMetrics().height() + 2)
        self.preview.setFixedHeight(self.preview.fontMetrics().height() * 2 + 2)
        self._place_on_screen()
        if self.expanded:
            self.timer.stop()
        elif restart_timer:
            self._resume_timer()

    def _place_on_screen(self):
        screen = QApplication.screenAt(self._anchor) if self._anchor is not None else None
        screen = screen or QApplication.primaryScreen()
        width, height = 350, (390 if self.expanded else 196)
        if screen is not None:
            rect = screen.availableGeometry()
            width = min(width, max(1, rect.width() - 16))
            height = min(height, max(1, rect.height() - 16))
            self.setFixedSize(width, height)
            self.move(clamp_position([rect.right() - width - 20,
                rect.bottom() - height - 20], width, height, [screen]))
        else:
            self.setFixedSize(width, height)

    def _resume_timer(self):
        if self.isVisible() and not self.expanded and not self.more_menu.isVisible():
            self.timer.start(20000)

    def present(self, notification, anchor=None):
        if self.settings["quiet_mode"]:
            return False
        self.notification = dict(notification)
        self.identifier = str(notification["id"])
        self._anchor = QPoint(anchor) if anchor is not None else None
        kind = notification.get("notification_kind", notification.get("kind", "received"))
        subject = "Shared note" if notification.get("entity_kind") == "note" else "Team message"
        self.heading.setText(subject + " reminder" if kind == "reminder" else "New " + subject.lower())
        self.title.setText(str(notification.get("title", "Team message")))
        self.title.setToolTip(self.title.text())
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
        self.details.setToolTip(self.details.text())
        self.details.setVisible(bool(detail))
        body = str(notification.get("body", ""))
        checklist = notification.get("checklist", [])
        if checklist:
            body += "\n\n" + "\n".join(f"[{'✓' if item.get('done') else ' '}] {item.get('quantity', 1)} × {item.get('text', '')}"
                for item in checklist[:100] if isinstance(item, dict))
        self.body.setPlainText(body.strip())
        self.preview.setText(body.strip())
        self.set_expanded(self._default_expanded(), restart_timer=False)
        self.show()
        self._resume_timer()
        return True

    def act(self, signal):
        if self.identifier:
            signal.emit(self.identifier)
        self.dismiss()

    def snooze(self):
        self.snooze_for(int(self.snooze_minutes.currentData()))

    def snooze_for(self, minutes):
        if self.identifier:
            self.snooze_requested.emit(self.identifier, minutes)
        self.dismiss()

    def dismiss(self):
        self.timer.stop()
        self.more_menu.hide()
        self.hide()

    def dismiss_if_inactive(self, active_message_ids):
        if self.identifier and self.identifier not in active_message_ids:
            self.dismiss()

    def enterEvent(self, event):
        self.timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._resume_timer()
        super().leaveEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def shutdown(self):
        self.dismiss()
