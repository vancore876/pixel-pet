"""Local, account-scoped reminders for overdue shared customer orders.

The office server remains the source of order details. This journal contains
only hashed account scopes, record IDs, tier numbers and timestamps; it never
stores customer names, order text or credentials. Showing a reminder does not
change an order or send a message to the customer or coworkers.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time

from PySide6.QtCore import QObject, QTimer, Signal

from config import team_data_directory
from work_chat import normalize_server_url

TIER_LABELS = ("Due now", "Over 1 hour late", "Over 1 day late")
SNOOZE_MINUTES = (15, 60, 1440)
MAX_ACCOUNTS = 32
MAX_ORDERS = 2000


def _timestamp(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and 0 <= value < 1e12)


def _identifier(value, length=32):
    return (isinstance(value, str) and len(value) == length
            and all(char in "0123456789abcdef" for char in value))


class OrderEscalationService(QObject):
    changed = Signal()
    notification = Signal(object)
    reset = Signal()
    error = Signal(str)

    def __init__(self, settings, shared_business, state_path=None,
                 can_notify=None, parent=None, *, clock=None):
        super().__init__(parent)
        self.settings = settings
        self.shared_business = shared_business
        self.path = Path(state_path) if state_path is not None else team_data_directory() / "order-escalations.json"
        self.can_notify = can_notify or (lambda: True)
        self.clock = clock or time.time
        self._saved = self._load()
        self._scope = self._origin = self._token = ""
        self._user_id = None
        self._generation = 0
        self._established = False
        self._stopped = False
        self._offered = self._active = None
        self._snapshot = []
        self._rows = []
        self._last_error = ""
        self.status = "Sign in to Work Chat to see overdue customer orders."
        self.timer = QTimer(self)
        self.timer.setInterval(15000)
        self.timer.timeout.connect(self.pump)
        self._next_timer = QTimer(self)
        self._next_timer.setSingleShot(True)
        self._next_timer.timeout.connect(self.pump)
        shared_business.service.changed.connect(self.pump)
        shared_business.status_changed.connect(self.pump)

    @property
    def rows(self):
        return [dict(row) for row in self._rows]

    def _load(self):
        try:
            if self.path.stat().st_size > 16 * 1024 * 1024:
                return {}
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            return {}
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("accounts"), dict):
            return {}
        saved = {}
        for scope, records in list(data["accounts"].items())[-MAX_ACCOUNTS:]:
            if not _identifier(scope, 64) or not isinstance(records, dict):
                continue
            valid = {}
            for identifier, row in list(records.items())[:MAX_ORDERS]:
                if (not _identifier(identifier) or not isinstance(row, dict)
                        or not _timestamp(row.get("due")) or row["due"] <= 0
                        or type(row.get("tier")) is not int or not -1 <= row["tier"] <= 2
                        or not _timestamp(row.get("snooze_until"))
                        or not _timestamp(row.get("delivered_at"))):
                    continue
                valid[identifier] = {key: row[key] for key in ("due", "tier", "snooze_until", "delivered_at")}
            saved[scope] = valid
        return saved

    def _save(self):
        if not self._scope:
            return
        current = self._saved.pop(self._scope, {})
        self._saved[self._scope] = current
        while len(self._saved) > MAX_ACCOUNTS:
            del self._saved[next(iter(self._saved))]
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent,
                                             prefix=".order-escalations-", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({"version": 1, "accounts": self._saved}, stream, separators=(",", ":"), allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
            self._last_error = ""
        except OSError:
            message = "Could not save your order reminder choices. Reminders may repeat after restarting."
            if message != self._last_error:
                self._last_error = message
                self.error.emit(message)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def set_session(self, origin, token, user=None):
        self.timer.stop()
        self._next_timer.stop()
        self._generation += 1
        self._scope = self._origin = self._token = ""
        self._user_id = None
        self._established = False
        self._offered = self._active = None
        self._snapshot = []
        self._rows = []
        self.status = "Sign in to Work Chat to see overdue customer orders."
        self.reset.emit()
        self.changed.emit()
        if (self._stopped or not token or not isinstance(user, dict)
                or type(user.get("id")) is not int or user["id"] <= 0):
            return
        try:
            self._origin = normalize_server_url(origin)
        except ValueError as exc:
            self.error.emit(str(exc))
            return
        self._token, self._user_id = token, user["id"]
        self._scope = hashlib.sha256(f"{self._origin}\n{self._user_id}".encode("utf-8")).hexdigest()
        self._saved.setdefault(self._scope, {})
        self.timer.start()
        self.pump()

    def _same_session(self):
        shared = self.shared_business
        try:
            origin = normalize_server_url(shared.api.base_url)
        except (ValueError, AttributeError):
            return False
        return (bool(self._scope) and self._token == shared.token
                and self._user_id == (shared.user or {}).get("id") and origin == self._origin)

    @staticmethod
    def _order(record):
        if (not isinstance(record, dict) or record.get("deleted") is not False
                or not _identifier(record.get("id")) or type(record.get("revision")) is not int
                or record["revision"] < 1):
            return None
        entry = record.get("entry")
        if (not isinstance(entry, dict) or entry.get("id") != record["id"]
                or entry.get("kind") != "order" or entry.get("order_type") == "quote"
                or entry.get("done") is True
                or entry.get("order_status") in ("delivered", "cancelled")):
            return None
        due = entry.get("order_due")
        if due is None:
            due = entry.get("next_due")
        if not _timestamp(due) or due <= 0:
            return None
        def text(key, fallback=""):
            value = entry.get(key)
            return value[:240] if isinstance(value, str) and value else fallback
        return {"id": record["id"], "due": due, "customer": text("customer", "Customer not named"),
                "order_ref": text("order_ref", text("title", "Customer order")),
                "title": text("title", "Customer order"), "order_status": text("order_status", "new")}

    def pump(self, *_):
        if self._stopped:
            return
        previous_rows, previous_status = self._rows, self.status
        self._rows = []
        if not self._scope:
            self.status = "Sign in to Work Chat to see overdue customer orders."
        elif not self._same_session():
            self._established = False
            self._snapshot = []
            self.status = "Waiting for this account's shared orders…"
        elif not self.shared_business.ready and not self._established:
            self.status = "Waiting for a complete office order list. Reconnect to load overdue orders."
        else:
            self._established = True
            cached = not self.shared_business.ready
            self.status = ("Offline · showing cached office orders. Dates and status may have changed."
                           if cached else "Live office orders · reminders are personal to this employee on this PC.")
            now = self.clock()
            # A paginated update can fail halfway through. Keep the last complete
            # snapshot until the controller has validated every remaining page.
            if not cached:
                self._snapshot = [order for record in self.shared_business._records.values()
                                  if (order := self._order(record)) is not None]
            orders = [dict(order) for order in self._snapshot]
            journal = self._saved[self._scope]
            live_ids = {row["id"] for row in orders}
            modified = False
            for identifier in list(journal):
                if identifier not in live_ids:
                    del journal[identifier]
                    modified = True
            for row in orders:
                identifier, due = row["id"], row["due"]
                state = journal.get(identifier)
                if state and state["due"] != due:
                    del journal[identifier]
                    state = None
                    modified = True
                if due > now:
                    continue
                tier = 2 if now - due >= 86400 else 1 if now - due >= 3600 else 0
                row.update(tier=tier, tier_label=TIER_LABELS[tier], cached=cached,
                           snooze_until=state["snooze_until"] if state else 0)
                self._rows.append(row)
            self._rows.sort(key=lambda row: (-row["tier"], row["due"], row["id"]))
            if modified:
                self._save()
        by_id = {row["id"]: row for row in self._rows}
        if self._active is not None:
            current = by_id.get(self._active["id"])
            if current is None or any(current[key] != self._active[key] for key in
                    ("due", "tier", "customer", "order_ref", "title", "order_status", "cached")):
                self._active = None
                self.reset.emit()
        if self._offered is not None:
            current = by_id.get(self._offered["id"])
            if current is None or any(current[key] != self._offered[key] for key in
                    ("due", "tier", "customer", "order_ref", "title", "order_status", "cached")):
                self._offered = None
                self.reset.emit()
        if self.settings["quiet_mode"] or not self.settings["note_reminders"]:
            if self._active is not None or self._offered is not None:
                self._active = self._offered = None
                self.reset.emit()
        if previous_rows != self._rows or previous_status != self.status:
            self.changed.emit()
        self._offer()

    def _offer(self):
        if (self._offered is not None or self._active is not None or not self._scope or self._stopped
                or self.settings["quiet_mode"] or not self.settings["note_reminders"]
                or not self.can_notify()):
            return
        now = self.clock()
        journal = self._saved[self._scope]
        for row in self._rows:
            state = journal.get(row["id"], {})
            until = state.get("snooze_until", 0)
            if until > now:
                continue
            if row["tier"] > state.get("tier", -1) or until:
                self._offered = dict(row, generation=self._generation)
                self.notification.emit(dict(self._offered))
                break

    def mark_notification_presented(self, payload):
        """Commit a tier only after the desktop actually displays its card."""
        if (not isinstance(payload, dict) or payload != self._offered or not self._same_session()
                or self.settings["quiet_mode"] or not self.settings["note_reminders"]):
            return False
        row = next((row for row in self._rows if row["id"] == payload["id"] and row["due"] == payload["due"]), None)
        if row is None:
            return False
        self._saved[self._scope][row["id"]] = {"due": row["due"], "tier": payload["tier"],
                                               "snooze_until": 0, "delivered_at": self.clock()}
        self._offered = None
        self._active = dict(payload)
        self._save()
        return True

    def release_notification(self, payload):
        """Retry refused cards later; advance after a displayed card closes."""
        if not isinstance(payload, dict):
            return
        if payload == self._offered:
            self._offered = None
        elif payload == self._active:
            self._active = None
            # Avoid opening the next card inside the previous card's button
            # handler, before its Open or Snooze request has been processed.
            self._next_timer.start(0)

    def snooze(self, identifier, minutes):
        if type(minutes) is not int or minutes not in SNOOZE_MINUTES or not self._same_session():
            return False
        row = next((row for row in self._rows if row["id"] == identifier), None)
        if row is None:
            return False
        previous = self._saved[self._scope].get(identifier, {})
        self._saved[self._scope][identifier] = {"due": row["due"], "tier": previous.get("tier", -1),
            "snooze_until": self.clock() + minutes * 60, "delivered_at": previous.get("delivered_at", 0)}
        self._save()
        if self._offered is not None and self._offered["id"] == identifier:
            self._offered = None
        if self._active is not None and self._active["id"] == identifier:
            self._active = None
            self.reset.emit()
        self.pump()
        return True

    def stop(self):
        self.set_session("", "", None)
        self._stopped = True
        self.timer.stop()
