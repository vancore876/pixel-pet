"""Consistent employee-chat keyboard behavior without dialog defaults."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QPlainTextEdit


class MessageInput(QPlainTextEdit):
    send_requested = Signal()

    def keyPressEvent(self, event):
        if (event.key() in (Qt.Key_Return, Qt.Key_Enter)
                and not event.modifiers() & (Qt.ShiftModifier | Qt.AltModifier | Qt.MetaModifier)):
            # Consume Enter even when sending is unavailable. It must never
            # activate another QDialog button or open a secondary window.
            event.accept()
            if not event.isAutoRepeat():
                self.send_requested.emit()
            return
        super().keyPressEvent(event)
