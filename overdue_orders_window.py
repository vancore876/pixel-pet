"""An overdue customer order board and a small, passive desktop reminder."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QDialog, QHBoxLayout,
    QHeaderView, QLabel, QMenu, QPushButton, QTableWidget, QTableWidgetItem,
    QToolButton, QVBoxLayout, QWidget)

from config import clamp_position
from ui_style import apply_window_style, window_style


def _label(text="", name=""):
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    if name:
        label.setObjectName(name)
    return label


def _button(text, callback):
    button = QPushButton(text)
    button.setAutoDefault(False)
    button.clicked.connect(callback)
    return button


def _when(timestamp):
    try:
        return datetime.fromtimestamp(timestamp).astimezone().strftime("%b %d, %I:%M %p")
    except (ValueError, OSError, OverflowError):
        return "Unknown date"


def _snooze_menu(parent, callback):
    menu = QMenu(parent)
    for label, minutes in (("In 15 minutes", 15), ("In 1 hour", 60), ("In 24 hours", 1440)):
        action = menu.addAction(label)
        action.triggered.connect(lambda checked=False, value=minutes: callback(value))
    return menu


class OverdueOrdersWindow(QDialog):
    open_requested = Signal(str)
    home_requested = Signal()

    def __init__(self, service, settings, parent=None):
        super().__init__(parent)
        self.service, self.settings = service, settings
        self.setWindowTitle("Jeffery · Overdue customer orders")
        self.resize(960, 580)
        self.setMinimumSize(680, 380)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        header = QHBoxLayout()
        header.addWidget(_label("Overdue customer orders", "pageHeading"), 1)
        self.home_button = _button("Home", self.home_requested.emit)
        header.addWidget(self.home_button)
        layout.addLayout(header)
        layout.addWidget(_label("Due now → 1 hour late → 1 day late. Quotes and completed, delivered or cancelled orders are excluded. "
                               "Snoozing changes only your reminders; it does not change an order or contact a customer.", "subtitle"))
        self.status = _label("", "hint")
        layout.addWidget(self.status)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(("Customer", "Order", "Due", "Escalation", "Order status", "Your reminder"))
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.itemDoubleClicked.connect(lambda *_: self.open_selected())
        layout.addWidget(self.table, 1)
        self.empty_hint = _label("", "hint")
        layout.addWidget(self.empty_hint)
        footer = QHBoxLayout()
        self.open_button = _button("Open order", self.open_selected)
        self.open_button.setObjectName("primary")
        footer.addWidget(self.open_button)
        self.snooze_button = QToolButton()
        self.snooze_button.setText("Remind me later")
        self.snooze_button.setPopupMode(QToolButton.InstantPopup)
        self.snooze_button.setMenu(_snooze_menu(self, self.snooze_selected))
        footer.addWidget(self.snooze_button)
        footer.addStretch(1)
        self.refresh_button = _button("Refresh office orders", service.shared_business.refresh)
        footer.addWidget(self.refresh_button)
        layout.addLayout(footer)
        service.changed.connect(self.refresh)
        service.reset.connect(self._close_menu)
        self.configure()
        self.refresh()

    def configure(self):
        apply_window_style(self, self.settings)

    def show_workspace(self):
        self.refresh()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _close_menu(self):
        self.snooze_button.menu().close()

    def _selected_id(self):
        item = self.table.item(self.table.currentRow(), 0)
        return item.data(Qt.UserRole) if item is not None else None

    def _selection_changed(self):
        selected = bool(self._selected_id())
        self.open_button.setEnabled(selected)
        self.snooze_button.setEnabled(selected)

    def refresh(self):
        selected = self._selected_id()
        rows = self.service.rows
        self.table.setRowCount(0)
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            later = row["snooze_until"] > self.service.clock()
            values = (row["customer"], row["order_ref"], _when(row["due"]), row["tier_label"],
                      row["order_status"].capitalize(), "Snoozed until " + _when(row["snooze_until"]) if later else "Active")
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.UserRole, row["id"])
                self.table.setItem(index, column, item)
            if row["id"] == selected:
                self.table.selectRow(index)
        self.status.setText(self.service.status)
        self.empty_hint.setText(f"{len(rows)} overdue customer order(s)." if rows else
            "No overdue orders in the last loaded office list." if self.service._established else
            "Orders appear here after this account's office list has loaded.")
        self.refresh_button.setEnabled(bool(self.service._token))
        self._selection_changed()

    def open_selected(self):
        identifier = self._selected_id()
        if identifier and any(row["id"] == identifier for row in self.service.rows):
            self.open_requested.emit(identifier)

    def snooze_selected(self, minutes):
        identifier = self._selected_id()
        if identifier:
            self.service.snooze(identifier, minutes)


class OverdueOrderPopup(QWidget):
    open_requested = Signal(str)
    snooze_requested = Signal(str, int)
    dismissed = Signal(object)

    def __init__(self, settings):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint
                         | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.settings = settings
        self.identifier = ""
        self.notification = {}
        self.setWindowTitle("Jeffery · Customer order reminder")
        self.setObjectName("overdueCard")
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)
        header = QHBoxLayout()
        self.heading = _label("Customer order due")
        self.heading.setStyleSheet("font-size: 13px; font-weight: 600;")
        header.addWidget(self.heading, 1)
        self.close_button = _button("×", self.clear)
        self.close_button.setFixedSize(26, 26)
        self.close_button.setAccessibleName("Dismiss this reminder")
        header.addWidget(self.close_button)
        layout.addLayout(header)
        self.subject = _label()
        self.subject.setMaximumHeight(42)
        layout.addWidget(self.subject)
        self.details = _label("", "hint")
        self.details.setMaximumHeight(36)
        layout.addWidget(self.details)
        footer = QHBoxLayout()
        self.open_button = _button("Open order", self.open_order)
        self.open_button.setObjectName("primary")
        footer.addWidget(self.open_button)
        footer.addStretch(1)
        self.snooze_button = QToolButton()
        self.snooze_button.setText("Later")
        self.snooze_button.setPopupMode(QToolButton.InstantPopup)
        self.snooze_button.setMenu(_snooze_menu(self, self.snooze_for))
        footer.addWidget(self.snooze_button)
        layout.addLayout(footer)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.clear)
        self.snooze_button.menu().aboutToShow.connect(self.timer.stop)
        self.snooze_button.menu().aboutToHide.connect(self._resume_timer)
        self.configure()

    def configure(self):
        visible = self.isVisible()
        self.setStyleSheet(window_style(self.settings) + """
            QWidget#overdueCard QPushButton, QWidget#overdueCard QToolButton {
                padding: 4px 7px; min-height: 18px;
            }
        """)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, self.settings["always_on_top"])
        if self.settings["quiet_mode"] or not self.settings["note_reminders"]:
            self.clear()
        elif visible and not self.isVisible():
            # Changing a native window flag hides a Qt top-level widget. Keep
            # its displayed card and timeout alive without requesting focus.
            self.show()

    def display(self, payload, position=None):
        if self.settings["quiet_mode"] or not self.settings["note_reminders"] or self.isVisible():
            return False
        self.notification = dict(payload)
        self.identifier = str(payload["id"])
        self.heading.setText("Customer order · " + payload["tier_label"])
        self.subject.setText((payload["customer"] + " · " + payload["order_ref"])[:240])
        self.details.setText("Due " + _when(payload["due"]) + (" · Offline cached order" if payload["cached"] else ""))
        screen = QApplication.screenAt(position) if position is not None else None
        screen = screen or QApplication.primaryScreen()
        width, height = 350, 178
        if screen is not None:
            rect = screen.availableGeometry()
            width, height = min(width, rect.width() - 16), min(height, rect.height() - 16)
            self.setFixedSize(max(1, width), max(1, height))
            self.move(clamp_position([rect.right() - width - 20, rect.bottom() - height - 20], width, height, [screen]))
        else:
            self.setFixedSize(width, height)
        self.show()
        self.timer.start(20000)
        return True

    def _resume_timer(self):
        if self.isVisible():
            self.timer.start(20000)

    def clear(self):
        previous = self.notification
        self.snooze_button.menu().close()
        self.timer.stop()
        self.hide()
        self.identifier = ""
        self.notification = {}
        self.heading.setText("Customer order reminder")
        self.subject.clear()
        self.details.clear()
        if previous:
            self.dismissed.emit(previous)

    def open_order(self):
        identifier = self.identifier
        self.clear()
        if identifier:
            self.open_requested.emit(identifier)

    def snooze_for(self, minutes):
        identifier = self.identifier
        self.clear()
        if identifier:
            self.snooze_requested.emit(identifier, minutes)

    def closeEvent(self, event):
        self.clear()
        event.accept()
