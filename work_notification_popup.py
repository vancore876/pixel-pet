"""A passive office-server message card, separate from shared-folder receipts."""
from __future__ import annotations

from PySide6.QtCore import Signal

from team_popup import TeamPopup


class WorkNotificationPopup(TeamPopup):
    """Reuse the desktop card, with Work Chat conversation routing and wording."""

    conversation_requested = Signal(object)

    def __init__(self, settings):
        super().__init__(settings)
        self.setWindowTitle("Jeffery · Work Chat message")
        self.open_requested.connect(self._open_conversation)
        self.hint.setText("Open Work Chat to reply. Acknowledge stops your reminders for this message.")

    def present(self, notification, anchor=None):
        if not self.settings["work_chat_notifications"]:
            return False
        message = dict(notification)
        team = message.get("recipient_id") is None
        message["title"] = "Team Room" if team else "Personal message"
        message["author_name"] = message.get("sender_username", "Coworker")
        if not super().present(message, anchor):
            return False
        subject = "Team Room message" if team else "Personal message"
        self.heading.setText(subject + " reminder" if message.get("notification_kind") == "reminder" else "New " + subject.lower())
        return True

    def configure(self):
        super().configure()
        if not self.settings["work_chat_notifications"]:
            self.dismiss()

    def _open_conversation(self, identifier):
        self.conversation_requested.emit(dict(self.notification))

    def clear_session(self):
        self.dismiss()
        self.identifier = ""
        self.notification = {}
        self.body.clear()
        self.preview.clear()
        self.title.clear()
        self.title.setToolTip("")
        self.details.clear()
        self.details.setToolTip("")
