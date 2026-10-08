"""Authenticated shared orders with version checks and a disposable local cache."""
from __future__ import annotations

import copy
import json
import time
import uuid
from urllib.parse import urlencode

from PySide6.QtCore import QObject, QTimer, Signal

from notes import MAX_NOTES, NoteStore
from work_chat import _ApiClient


class SharedBusinessController(QObject):
    status_changed = Signal(str, bool)
    session_expired = Signal()

    def __init__(self, service, parent=None, api=None):
        super().__init__(parent)
        self.service, self.store = service, service.store
        self.api = api or _ApiClient(self)
        self.token = ""
        self.user = None
        self.ready = False
        self._generation = 0
        self._cursor = 0
        self._records = {}
        self._cache_ids = set()
        self._editing_id, self._editing_revision = None, 0
        self._new_identifier = uuid.uuid4().hex
        self._polling = self._writing = self._closed = False
        self._write_callback = None
        self.manifest = self.store.path.parent / "business-cache.json"
        self._recover_cache()
        self.timer = QTimer(self)
        self.timer.setInterval(2500)
        self.timer.timeout.connect(self.refresh)

    def _recover_cache(self):
        try:
            raw = json.loads(self.manifest.read_text(encoding="utf-8"))
            ids = raw.get("ids", []) if isinstance(raw, dict) else []
            self._cache_ids = {identifier for identifier in ids
                               if isinstance(identifier, str) and len(identifier) == 32
                               and all(char in "0123456789abcdef" for char in identifier)}
            self._clear_cache()
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError):
            # A damaged cache manifest must not delete unrelated personal data.
            self._cache_ids.clear()

    def _write_manifest(self, identifiers):
        self.manifest.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.manifest.with_suffix(".tmp")
        temporary.write_text(json.dumps({"ids": sorted(identifiers)}) + "\n", encoding="utf-8")
        temporary.replace(self.manifest)

    def _clear_cache(self):
        identifiers = self._cache_ids.copy()
        if identifiers:
            def remove():
                self.store.state["notes"] = [note for note in self.store.notes
                    if note["id"] not in identifiers or note.get("kind") not in ("order", "list")]
            self.store.transaction(remove)
        self._write_manifest(set())
        self._cache_ids.clear()

    def set_session(self, origin, token, user=None):
        if self._closed:
            return
        self._generation += 1
        self.timer.stop()
        self.api.cancel_all()
        pending, self._write_callback = self._write_callback, None
        if pending is not None:
            pending(None, "The work account changed while saving. Check the server before retrying; your draft is preserved.")
        self._polling = self._writing = False
        self.ready = False
        self.token, self.user = token, user
        self.api.base_url = origin
        self._cursor = 0
        self._records.clear()
        self._editing_id, self._editing_revision = None, 0
        self._new_identifier = uuid.uuid4().hex
        try:
            self._clear_cache()
        except (OSError, ValueError) as error:
            self.token = ""
            self.status_changed.emit("Could not clear the business cache: " + str(error), False)
            return
        self.service.changed.emit("")
        if not token:
            self.status_changed.emit("Sign in to Work Chat to use shared orders and checklists.", False)
            return
        self.status_changed.emit("Loading Famous Twins orders from the office server…", False)
        self.timer.start()
        self.refresh()

    def mark_editing(self, identifier):
        self._editing_id = identifier
        self._editing_revision = self._records.get(identifier, {}).get("revision", 0)
        if identifier is None:
            self._new_identifier = uuid.uuid4().hex

    def is_business(self, identifier):
        note = self.store.find(identifier)
        return bool(note and note.get("kind") in ("order", "list"))

    def _apply_record(self, record):
        if not isinstance(record, dict):
            raise ValueError("Invalid shared business record.")
        identifier, revision = record.get("id"), record.get("revision")
        if (not isinstance(identifier, str) or len(identifier) != 32
                or any(char not in "0123456789abcdef" for char in identifier)
                or type(revision) is not int or revision < 1 or type(record.get("deleted")) is not bool):
            raise ValueError("Invalid shared business version.")
        if revision <= self._records.get(identifier, {}).get("revision", 0):
            return self.store.find(identifier)
        known = identifier in self._cache_ids
        identifiers = self._cache_ids.copy()
        note = None
        if record["deleted"]:
            identifiers.discard(identifier)
        else:
            entry = record.get("entry")
            if (not isinstance(entry, dict) or entry.get("id") != identifier
                    or entry.get("kind") not in ("order", "list") or entry.get("document_id")):
                raise ValueError("Invalid shared order or checklist.")
            note = NoteStore.validate_note(entry)
            if note is None:
                raise ValueError("The shared record is incomplete.")
            old = self.store.find(identifier)
            if old and old.get("kind") not in ("order", "list"):
                raise ValueError("A shared record conflicts with a personal writing ID. Personal writing was preserved.")
            if old:
                for field in ("pinned", "pin_position", "ai_guidance", "reminder_history"):
                    note[field] = copy.deepcopy(old.get(field, note.get(field)))
            elif len(self.store.notes) >= MAX_NOTES:
                raise ValueError("Your notebook is full. Export or remove personal entries before loading more shared records.")
            note["unannounced"] = False
            identifiers.add(identifier)
            candidate = {**self.store.state, "notes": [row for row in self.store.notes if row['id'] != identifier] + [note]}
            if len(json.dumps(candidate, indent=2, ensure_ascii=False, allow_nan=False).encode('utf-8')) > 120 * 1024 * 1024:
                raise ValueError("The local business cache is full. Export or remove older personal entries.")
        # Record cache ownership first so a crash cannot turn cached business
        # records into unmarked personal entries on the next launch.
        self._write_manifest(identifiers | self._cache_ids)

        def apply():
            old = self.store.find(identifier)
            if record["deleted"]:
                if known:
                    self.store.state["notes"] = [row for row in self.store.notes if row["id"] != identifier]
            elif old:
                old.update(note)
            else:
                self.store.state["notes"].append(note)
        self.store.transaction(apply)
        self._cache_ids = identifiers
        self._write_manifest(identifiers)
        self._records[identifier] = copy.deepcopy(record)
        return self.store.find(identifier)

    def refresh(self):
        if self._closed or not self.token or self._polling or self._writing:
            return
        self._polling = True
        generation, start = self._generation, self._cursor

        def received(data, error, status):
            if generation != self._generation or self._closed:
                return
            self._polling = False
            if error:
                self.ready = False
                self.status_changed.emit("Shared business connection: " + error, False)
                if status == 401:
                    self.set_session(self.api.base_url, "", None)
                    self.session_expired.emit()
                return
            try:
                if (not isinstance(data, dict) or not isinstance(data.get("entries"), list)
                        or len(data["entries"]) > 3 or type(data.get("cursor")) is not int
                        or data["cursor"] < start or type(data.get("has_more")) is not bool):
                    raise ValueError("Invalid shared business response.")
                previous = start
                for record in data["entries"]:
                    if not isinstance(record, dict) or type(record.get("revision")) is not int or record["revision"] <= previous:
                        raise ValueError("Shared business revisions arrived out of order.")
                    previous = record["revision"]
                    self._apply_record(record)
                if data["cursor"] != previous or (data["has_more"] and not data["entries"]):
                    raise ValueError("Invalid shared business cursor.")
                self._cursor = data["cursor"]
                self.ready = not data["has_more"]
                self.service.changed.emit("")
                self.status_changed.emit("Shared with coworkers · " + (self.user or {}).get("username", "Office account")
                                         if self.ready else "Loading shared business records…", self.ready)
                if data["has_more"]:
                    QTimer.singleShot(0, self.refresh)
            except (OSError, ValueError, TypeError) as failure:
                self.ready = False
                self.status_changed.emit("Could not load shared business records: " + str(failure), False)
        self.api.request("GET", "/api/business?" + urlencode({"after_revision": start, "limit": 3}),
                         None, received, self.token)

    def _write(self, method, identifier, entry, callback):
        if not self.token or not self.ready or self._closed:
            callback(None, "Sign in to Work Chat and wait for shared orders to load before saving.")
            return False
        if self._writing:
            callback(None, "Wait for the current shared change to finish.")
            return False
        expected = (0 if self._editing_id is None and identifier == self._new_identifier else
                    self._editing_revision if identifier == self._editing_id
                    else self._records.get(identifier, {}).get("revision", 0))
        self._writing = True
        self._write_callback = callback
        generation = self._generation
        payload = {"expected_revision": expected}
        if entry is not None:
            payload["entry"] = entry
        self.status_changed.emit("Saving to the office server…", True)

        def received(data, error, status):
            if generation != self._generation or self._closed:
                return
            self._writing = False
            self._write_callback = None
            if error:
                message = ("Another coworker changed this record. Your draft is preserved. Use Reload saved to review the latest version."
                           if status == 409 else "The server did not confirm this change: " + error +
                           " Check the latest saved version before retrying.")
                callback(None, message)
                if status == 401:
                    self.set_session(self.api.base_url, "", None)
                    self.session_expired.emit()
                else:
                    self.refresh()
                return
            try:
                if not isinstance(data, dict) or data.get("id") != identifier:
                    raise ValueError("Invalid save acknowledgment.")
                note = self._apply_record(data)
                if identifier == self._editing_id:
                    self._editing_revision = data["revision"]
                self.service.changed.emit(identifier)
                callback(note, "")
                self.status_changed.emit("Saved on the office server · visible to coworkers", True)
                # A POST/PUT echo must never skip other people's unseen updates.
                self.refresh()
            except (OSError, ValueError, TypeError) as failure:
                callback(None, "The server saved the change, but the local cache could not be updated: " + str(failure))
                self.refresh()
        self.api.request(method, "/api/business/" + identifier, payload, received, self.token)
        return True

    def save(self, identifier, title, body, repeat, due, details, callback):
        try:
            now = time.time()
            old = self.store.find(identifier) if identifier else None
            if old:
                entry = copy.deepcopy(old)
                entry.update(details or {})
                entry.update(title=title.strip(), body=body.strip(), repeat_minutes=repeat,
                             next_due=due if due is not None else now + repeat * 60 if repeat else None, updated=now)
                if (entry["title"], entry["body"]) != (old["title"], old["body"]):
                    entry["written"] = now
            else:
                entry = self.store.new_note(title, body, repeat, due, now, details=details)
                identifier = entry["id"] = self._new_identifier
            if entry.get("kind") not in ("order", "list") or entry.get("document_id"):
                raise ValueError("Only orders and checklists can be shared; writing and documents stay personal.")
            return self._write("PUT", identifier, entry, callback)
        except (OSError, ValueError, TypeError) as error:
            callback(None, str(error))
            return False

    def modify(self, identifier, changes, callback):
        old = self.store.find(identifier)
        if not old:
            callback(None, "This business record is no longer available.")
            return False
        if set(changes) <= {"pinned", "pin_position"}:
            try:
                note = self.service.modify(identifier, **changes)
                callback(note, "")
                return True
            except (OSError, ValueError) as error:
                callback(None, str(error))
                return False
        entry = copy.deepcopy(old)
        entry.update(changes)
        entry["updated"] = time.time()
        return self._write("PUT", identifier, entry, callback)

    def complete(self, identifier, done, callback):
        old = self.store.find(identifier) or {}
        repeat = old.get("repeat_minutes", 0)
        changes = {"done": done, "next_due": None if done or not repeat else time.time() + repeat * 60}
        if old.get("kind") == "order":
            changes["order_status"] = "delivered" if done else "new"
        return self.modify(identifier, changes, callback)

    def delete(self, identifier, callback):
        return self._write("DELETE", identifier, None, callback)

    def shutdown(self):
        if self._closed:
            return
        self.set_session(self.api.base_url, "", None)
        self._closed = True
        self.timer.stop()
        self.api.cancel_all()
