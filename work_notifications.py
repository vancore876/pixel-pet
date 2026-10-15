"""Background Work Chat delivery, independent of the selected conversation.

Only delivery watermarks are stored on disk. Message bodies and login tokens
stay in memory; a queued card advances the durable watermark only when the
desktop confirms that it was shown. Restarting during quiet mode therefore
fetches those messages again from the authenticated server.
"""
from __future__ import annotations

from collections import deque
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import urlencode

from PySide6.QtCore import QObject, QTimer, Signal

from config import team_data_directory
from work_chat import USERNAME_RE, _ApiClient, _positive_id, normalize_server_url

MAX_QUEUED_NOTIFICATIONS = 100
MAX_REMINDERS = 500
MAX_SAVED_ACCOUNTS = 100
FEED_BATCH_LIMIT = 50


class WorkNotificationService(QObject):
    """Fetch incoming team/direct messages while the chat window is hidden."""

    notification = Signal(object)
    error = Signal(str)
    session_ended = Signal()
    auth_expired = Signal()

    def __init__(self, settings, local_state_path=None, can_notify=lambda: True, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.can_notify = can_notify
        self.path = Path(local_state_path) if local_state_path is not None else team_data_directory() / "work-notifications.json"
        self.api = _ApiClient(self)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.pump)
        self._saved = self._load()
        self._generation = 0
        self._scope = ""
        self._token = ""
        self._user_id = None
        self._cursor = None
        self._queue = deque()
        self._pending = None
        self._request_pending = False
        self._reminders = {}
        self._last_error = ""
        self._stopped = False

    def _load(self):
        try:
            if self.path.stat().st_size > 32_768:
                return {}
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {key: value for key, value in data.items()
                if isinstance(key, str) and len(key) == 64 and all(c in "0123456789abcdef" for c in key)
                and isinstance(value, int) and not isinstance(value, bool) and value >= 0}

    @staticmethod
    def _account_scope(server, user_id):
        return hashlib.sha256(f"{server}\n{user_id}".encode("utf-8")).hexdigest()

    def _save_cursor(self, cursor):
        if not self._scope or not isinstance(cursor, int):
            return
        saved = dict(self._saved)
        saved.pop(self._scope, None)
        saved[self._scope] = max(cursor, self._saved.get(self._scope, 0))
        while len(saved) > MAX_SAVED_ACCOUNTS:
            del saved[next(iter(saved))]
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent,
                                             prefix=".work-notifications-", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(saved, stream, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
            self._saved = saved
        except OSError:
            self._report_error("Work Chat could not save its notification position. Some popups may repeat after restarting.")
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def _report_error(self, text):
        if text != self._last_error:
            self._last_error = text
            self.error.emit(text)

    def session_changed(self, server, token, user):
        """Attach to the authenticated account; logout drops all message text."""
        self._generation += 1
        self.api.cancel_all()
        self.timer.stop()
        self._request_pending = False
        self._queue.clear()
        self._reminders.clear()
        self._pending = None
        self._token = ""
        self._scope = ""
        self._user_id = None
        self._cursor = None
        self._last_error = ""
        self.session_ended.emit()
        if self._stopped or not token or not isinstance(user, dict) or not _positive_id(user.get("id")):
            return
        try:
            self.api.base_url = normalize_server_url(server)
        except ValueError as exc:
            self._report_error(str(exc))
            return
        self._token = token
        self._user_id = user["id"]
        self._scope = self._account_scope(self.api.base_url, self._user_id)
        self._cursor = self._saved.get(self._scope)
        baseline = user.get("notification_cursor")
        if (self._cursor is None and isinstance(baseline, int)
                and not isinstance(baseline, bool) and baseline >= 0):
            # The authenticated server captures this in its login transaction.
            # Using a later baseline request would lose messages arriving
            # between authentication and the first background feed response.
            self._cursor = baseline
            self._save_cursor(baseline)
        self.timer.start()
        self.pump()

    def _valid_message(self, message):
        if (not isinstance(message, dict) or not _positive_id(message.get("id"))
                or not _positive_id(message.get("sender_id")) or message["sender_id"] == self._user_id
                or not isinstance(message.get("sender_username"), str)
                or not USERNAME_RE.fullmatch(message["sender_username"])
                or not isinstance(message.get("body"), str) or not 1 <= len(message["body"]) <= 4000
                or not isinstance(message.get("created_at"), str) or len(message["created_at"]) > 80):
            return False
        recipient = message.get("recipient_id")
        return recipient is None or (_positive_id(recipient) and recipient == self._user_id)

    def pump(self):
        if self._stopped or not self._token:
            return
        self._offer_notification()
        remaining = MAX_QUEUED_NOTIFICATIONS - len(self._queue)
        if self._request_pending or remaining <= 0:
            return
        generation = self._generation
        requested_cursor = self._cursor
        limit = min(FEED_BATCH_LIMIT, remaining)
        query = {"limit": limit}
        if requested_cursor is not None:
            query["after_id"] = requested_cursor
        self._request_pending = True

        def received(data, error, status):
            if generation != self._generation or self._stopped:
                return
            self._request_pending = False
            if error:
                self._report_error(error)
                if status == 401:
                    self.session_changed("", "", None)
                    self.auth_expired.emit()
                return
            messages = data.get("messages") if isinstance(data, dict) else None
            cursor = data.get("cursor") if isinstance(data, dict) else None
            if (not isinstance(messages, list) or len(messages) > limit
                    or not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0
                    or (requested_cursor is not None and cursor < requested_cursor)
                    or not all(self._valid_message(row) for row in messages)
                    or (requested_cursor is None and messages)):
                self._report_error("The Work Chat server returned an invalid notification list.")
                return
            identifiers = [row["id"] for row in messages]
            if (identifiers != sorted(set(identifiers))
                    or (identifiers and (identifiers[0] <= (requested_cursor or 0) or cursor != identifiers[-1]))):
                self._report_error("The Work Chat server returned an invalid notification position.")
                return
            self._last_error = ""
            self._cursor = cursor
            for row in messages:
                self._queue.append({**row, "peer_id": None if row.get("recipient_id") is None else row["sender_id"],
                                    "notification_kind": "received", "_generation": generation})
            # No queued cards are skipped when the server reports an empty page.
            if not self._queue:
                self._save_cursor(cursor)
            self._offer_notification()
            if data.get("has_more") is True and len(self._queue) < MAX_QUEUED_NOTIFICATIONS:
                QTimer.singleShot(0, self.pump)

        self.api.request("GET", "/api/notifications?" + urlencode(query), None, received, self._token)

    def _offer_notification(self):
        if (self._pending is not None or self.settings["quiet_mode"]
                or not self.settings["work_chat_notifications"] or not self.can_notify()):
            return
        candidate = self._queue[0] if self._queue else None
        if candidate is None:
            now = time.time()
            due = sorted((row for row in self._reminders.values() if row["_due"] <= now), key=lambda row: row["_due"])
            candidate = {**due[0], "notification_kind": "reminder"} if due else None
        if candidate is not None:
            self._pending = candidate
            self.notification.emit(dict(candidate))

    def notification_active(self, notification):
        return bool(self._token and isinstance(notification, dict)
                    and notification.get("_generation") == self._generation)

    def mark_notification_presented(self, notification):
        """Called only after the popup has accepted and displayed this card."""
        if not self._matches_pending(notification):
            return False
        pending, self._pending = self._pending, None
        identifier = pending["id"]
        if pending["notification_kind"] == "received":
            if not self._queue or self._queue[0]["id"] != identifier:
                return False
            self._queue.popleft()
            self._save_cursor(identifier)
        minutes = self.settings["work_chat_reminder_minutes"]
        if minutes > 0:
            self._reminders.pop(identifier, None)
            self._reminders[identifier] = {**pending, "_due": time.time() + minutes * 60}
            while len(self._reminders) > MAX_REMINDERS:
                del self._reminders[next(iter(self._reminders))]
        return True

    def _matches_pending(self, notification):
        return bool(self._pending and self.notification_active(notification)
                    and notification.get("id") == self._pending["id"]
                    and notification.get("notification_kind") == self._pending["notification_kind"])

    def release_notification(self, notification=None):
        if notification is None or self._matches_pending(notification):
            self._pending = None

    def acknowledge(self, identifier):
        self._reminders.pop(int(identifier), None)
        if self._pending and self._pending["id"] == int(identifier):
            self._pending = None

    def snooze(self, identifier, minutes):
        row = self._reminders.get(int(identifier))
        if row is not None:
            row["_due"] = time.time() + max(1, min(1440, int(minutes))) * 60
        if self._pending and self._pending["id"] == int(identifier):
            self._pending = None

    def stop(self):
        self._stopped = True
        self.session_changed("", "", None)
