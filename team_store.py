"""Local, durable work-team events and employee-specific reminder state.

The database belongs on each computer, never on the network share. Events are
immutable and form a small causal graph per entity; concurrent edits stay
available instead of being discarded according to computer clocks.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
import time
import uuid
from pathlib import Path


MAX_EVENT_BYTES = 256 * 1024
MAX_CHARACTERS = 1_000_000_000
KINDS = {"note", "message", "profile", "receipt"}


def _identifier(value):
    return isinstance(value, str) and len(value) == 32 and all(c in "0123456789abcdef" for c in value)


def _digest(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, OverflowError, RecursionError) as exc:
        raise ValueError("Work-team data must be valid JSON.") from exc


def _timestamp(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 253_402_300_799
    except OverflowError:
        return False


def _text(value, limit):
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise ValueError("Work-team text is invalid or too long.")
    return value


class TeamStore:
    def __init__(self, db_path):
        path = Path(db_path)
        from config import _network_location
        if str(path).startswith(("\\\\", "//")) or _network_location(path):
            raise ValueError("The work-team cache must be on this computer, not a network share.")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), timeout=10, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(
              event_id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,kind TEXT NOT NULL,
              entity_id TEXT NOT NULL,wire TEXT NOT NULL,local INTEGER NOT NULL,
              published INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS event_entity ON events(workspace_id,kind,entity_id);
            CREATE TABLE IF NOT EXISTS event_parents(event_id TEXT NOT NULL,parent_id TEXT NOT NULL,PRIMARY KEY(event_id,parent_id));
            CREATE INDEX IF NOT EXISTS parent_lookup ON event_parents(parent_id);
        """)
        with self._db:
            for key in ("device_id", "employee_id"):
                self._db.execute("INSERT OR IGNORE INTO metadata VALUES(?,?)", (key, uuid.uuid4().hex))
            self._db.execute("INSERT OR IGNORE INTO metadata VALUES('sequence','0')")
        self._closed = False
        self._scope_cache = {}

    def close(self):
        with self._lock:
            if not self._closed:
                self._db.close()
                self._closed = True

    def _meta(self, key, default=""):
        row = self._db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def identity(self):
        with self._lock:
            return {key: self._meta(key) for key in ("employee_id", "device_id", "name", "workspace_id")}

    def configure(self, workspace_id, name, employee_id=None):
        if not _identifier(workspace_id) or employee_id is not None and not _identifier(employee_id):
            raise ValueError("Choose a valid work-team and employee identity.")
        name = _text(name.strip(), 100)
        if not name:
            raise ValueError("Enter your employee name.")
        with self._lock, self._db:
            for key, value in (("workspace_id", workspace_id), ("name", name)):
                self._db.execute("INSERT OR REPLACE INTO metadata VALUES(?,?)", (key, value))
            if employee_id:
                self._db.execute("INSERT OR REPLACE INTO metadata VALUES('employee_id',?)", (employee_id,))
        return self.identity()

    @staticmethod
    def validate_event(event):
        if not isinstance(event, dict) or event.get("schema") != 1:
            raise ValueError("Unsupported work-team event.")
        if any(not _identifier(event.get(key)) for key in
               ("event_id", "workspace_id", "entity_id", "actor_id", "device_id")):
            raise ValueError("Invalid work-team event identity.")
        if not isinstance(event.get("kind"), str) or event["kind"] not in KINDS or event.get("operation") not in ("put", "delete"):
            raise ValueError("Invalid work-team operation.")
        if type(event.get("sequence")) is not int or event["sequence"] < 1 or not _timestamp(event.get("created")):
            raise ValueError("Invalid work-team event sequence or date.")
        parents = event.get("parents")
        if not isinstance(parents, list) or len(parents) > 2000 or any(not _identifier(p) for p in parents) or len(set(parents)) != len(parents) or event["event_id"] in parents:
            raise ValueError("Invalid work-team revision references.")
        payload = event.get("payload")
        if not isinstance(payload, dict) or len(_json(event).encode("utf-8")) > MAX_EVENT_BYTES:
            raise ValueError("Work-team event exceeds its safe size limit.")
        if event["operation"] == "delete":
            if event["kind"] not in {"note", "message"}:
                raise ValueError("This work-team record cannot be deleted.")
            return event
        kind = event["kind"]
        if kind == "profile":
            if event["entity_id"] != event["actor_id"]:
                raise ValueError("An employee may only update their own profile.")
            _text(payload.get("name"), 100)
        elif kind in {"note", "message"}:
            if payload.get("id", event["entity_id"]) != event["entity_id"]:
                raise ValueError("Shared note identity differs from its event.")
            _text(payload.get("title", ""), 200)
            _text(payload.get("body", ""), 10_000)
            for field, limit in (("customer", 100), ("contact", 100), ("order_ref", 80)):
                _text(payload.get(field, ""), limit)
            for field in ("created", "written"):
                if field in payload and not _timestamp(payload[field]):
                    raise ValueError("Invalid shared note date.")
            for field in ("done", "notify"):
                if field in payload and type(payload[field]) is not bool:
                    raise ValueError("Invalid shared note status.")
            if payload.get("kind", "note") not in ("note", "order", "list"):
                raise ValueError("Invalid shared note type.")
            if payload.get("order_status", "new") not in ("new", "preparing", "ready", "delivered", "cancelled", "canceled"):
                raise ValueError("Invalid customer order status.")
            if payload.get("note_id", "") and not _identifier(payload["note_id"]):
                raise ValueError("Invalid linked note identity.")
            checklist = payload.get("checklist", [])
            if not isinstance(checklist, list) or len(checklist) > 100:
                raise ValueError("Invalid customer order checklist.")
            item_ids = set()
            for item in checklist:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"] or len(item["id"]) > 64 or item["id"] in item_ids:
                    raise ValueError("Invalid checklist item identity.")
                item_ids.add(item["id"])
                _text(item.get("text"), 200)
                if type(item.get("done", False)) is not bool or type(item.get("quantity", 1)) is not int or not 1 <= item.get("quantity", 1) <= 9999:
                    raise ValueError("Invalid checklist item quantity or status.")
            recipients = payload.get("recipients", [])
            if not isinstance(recipients, list) or len(recipients) > 2000 or any(not _identifier(x) for x in recipients):
                raise ValueError("Invalid employee recipients.")
            repeat = payload.get("repeat_minutes", 30)
            if type(repeat) is not int or not 0 <= repeat <= 10080:
                raise ValueError("Invalid work-team reminder interval.")
            for key in ("due_at", "order_due"):
                if payload.get(key) is not None and not _timestamp(payload[key]):
                    raise ValueError("Invalid work-team reminder date.")
            document = payload.get("document")
            if document is not None and (not isinstance(document, dict) or not _digest(document.get("sha256"))
                or type(document.get("characters")) is not int or not 0 <= document["characters"] <= MAX_CHARACTERS):
                raise ValueError("Invalid shared document metadata.")
        else:
            if payload.get("employee_id") != event["actor_id"] or not _identifier(payload.get("entity_id")) or payload.get("entity_kind") not in ("note", "message"):
                raise ValueError("Invalid employee receipt identity.")
            expected = TeamStore._receipt_id(payload["employee_id"], payload["entity_kind"], payload["entity_id"])
            if expected != event["entity_id"] or payload.get("action") not in ("presented", "read", "snooze", "acknowledge") or not _timestamp(payload.get("at")):
                raise ValueError("Invalid employee receipt.")
            if not _digest(payload.get("scope")):
                raise ValueError("Invalid employee receipt schedule generation.")
            if payload["action"] == "presented" and not _digest(payload.get("occurrence_id")):
                raise ValueError("Invalid notification occurrence.")
            if payload.get("next_due") is not None and not _timestamp(payload["next_due"]):
                raise ValueError("Invalid employee reminder date.")
        return event

    def _workspace(self):
        result = self._meta("workspace_id")
        if not result:
            raise ValueError("Connect to a work team first.")
        return result

    def _events(self, kind, entity_id):
        return [json.loads(row[0]) for row in self._db.execute(
            "SELECT wire FROM events WHERE workspace_id=? AND kind=? AND entity_id=? ORDER BY event_id",
            (self._workspace(), kind, entity_id))]

    def _view(self, kind, entity_id):
        events = self._events(kind, entity_id)
        if not events:
            return None
        referenced = {parent for event in events for parent in event["parents"]}
        heads = [event for event in events if event["event_id"] not in referenced]
        if not heads:
            raise ValueError("Work-team revisions contain a cycle.")
        # A concurrent deletion wins until an explicit reviewed restoration.
        deleted = any(e["operation"] == "delete" for e in heads)
        chosen = max(heads, key=lambda e: (e["operation"] == "delete", e["event_id"]))
        variants = [{"revision": e["event_id"], "deleted": e["operation"] == "delete", "payload": e["payload"], "actor_id": e["actor_id"]} for e in heads]
        return {"id": entity_id, "kind": kind, "payload": chosen["payload"], "revisions": sorted(e["event_id"] for e in heads),
                "deleted": deleted, "conflict": len({_json((e["operation"], e["payload"])) for e in heads}) > 1,
                "variants": variants, "versions": variants, "winner_revision": chosen["event_id"],
                "author_id": chosen["actor_id"], "created": chosen["created"]}

    def get_entity(self, kind, entity_id):
        if kind not in KINDS or not _identifier(entity_id):
            raise ValueError("Invalid work-team entity.")
        with self._lock:
            return self._view(kind, entity_id)

    def entities(self, kind):
        if kind not in KINDS:
            raise ValueError("Invalid work-team entity kind.")
        with self._lock:
            ids = [row[0] for row in self._db.execute("SELECT DISTINCT entity_id FROM events WHERE workspace_id=? AND kind=? ORDER BY entity_id", (self._workspace(), kind))]
            return [self._view(kind, entity_id) for entity_id in ids]

    def conflicts(self):
        return [entity for kind in ("note", "message") for entity in self.entities(kind) if entity["conflict"]]

    def _put(self, kind, entity_id, payload, operation="put", base_revisions=None, now=None, restore=False):
        with self._lock, self._db:
            current = self._view(kind, entity_id)
            if operation == "put" and current and current["deleted"] and not restore:
                raise ValueError("This shared record was deleted. Review its preserved versions before restoring it.")
            parents = current["revisions"] if current and base_revisions is None else list(base_revisions or [])
            if kind in {"note", "message"} and operation == "put" and current and current["conflict"] and base_revisions is None:
                parents = [current["winner_revision"]]
            if any(not _identifier(parent) for parent in parents):
                raise ValueError("Invalid base revision.")
            for parent in parents:
                row = self._db.execute("SELECT workspace_id,kind,entity_id FROM events WHERE event_id=?", (parent,)).fetchone()
                if row is None or tuple(row) != (self._workspace(), kind, entity_id):
                    raise ValueError("Review this record's current revisions before changing it.")
            seq = int(self._meta("sequence")) + 1
            event = {"schema": 1, "workspace_id": self._workspace(), "event_id": uuid.uuid4().hex,
                     "kind": kind, "entity_id": entity_id, "operation": operation,
                     "actor_id": self._meta("employee_id"), "device_id": self._meta("device_id"),
                     "sequence": seq, "created": time.time() if now is None else now,
                     "parents": sorted(set(parents)), "payload": json.loads(_json(payload))}
            self.validate_event(event)
            self._db.execute("INSERT INTO events VALUES(?,?,?,?,?,1,0)", (event["event_id"], event["workspace_id"], kind, entity_id, _json(event)))
            self._db.executemany("INSERT INTO event_parents VALUES(?,?)", ((event["event_id"], parent) for parent in event["parents"]))
            self._db.execute("UPDATE metadata SET value=? WHERE key='sequence'", (str(seq),))
            if kind in {"note", "message"}:
                self._scope_cache.clear()
        return event

    def put_note(self, note, base_revisions=None):
        note = json.loads(_json(note))
        identifier = note.get("id") or uuid.uuid4().hex
        note["id"] = identifier
        if not _identifier(identifier):
            raise ValueError("Invalid shared note ID.")
        return self._put("note", identifier, note, base_revisions=base_revisions)

    def delete_note(self, note_id, base_revisions=None):
        current = self.get_entity("note", note_id)
        return self._put("note", note_id, current["payload"] if current else {}, "delete", base_revisions)

    def resolve_entity(self, kind, entity_id, revision):
        current = self.get_entity(kind, entity_id)
        if current is None:
            raise ValueError("This shared record is unavailable.")
        variant = next((v for v in current["variants"] if v["revision"] == revision), None)
        if variant is None:
            raise ValueError("Review the latest conflicting versions first.")
        return self._put(kind, entity_id, variant["payload"], "delete" if variant["deleted"] else "put", current["revisions"], restore=True)

    def publish_profile(self, name=None):
        identity = self.identity()
        name = identity["name"] if name is None else _text(name.strip(), 100)
        return self._put("profile", identity["employee_id"], {"name": name})

    def send_message(self, title, body, recipients, first_due=None, repeat_minutes=30, customer="", note_id=""):
        title, body = _text(title.strip(), 200), _text(body.strip(), 10_000)
        if not title and not body:
            raise ValueError("Write a message before sending it.")
        if not _timestamp(first_due) and first_due is not None:
            raise ValueError("Choose a valid first reminder time.")
        now = time.time()
        payload = {"title": title, "body": body, "recipients": list(recipients), "repeat_minutes": repeat_minutes,
                   "due_at": first_due if first_due is not None else now + repeat_minutes * 60 if repeat_minutes else None,
                   "customer": _text(customer, 100), "note_id": note_id, "created": now}
        if note_id and not _identifier(note_id):
            raise ValueError("Invalid linked note.")
        return self._put("message", uuid.uuid4().hex, payload, now=now)

    def ingest_event(self, event):
        self.validate_event(event)
        wire = _json(event)
        with self._lock, self._db:
            if event["workspace_id"] != self._workspace():
                raise ValueError("This event belongs to another work team.")
            prior = self._db.execute("SELECT wire FROM events WHERE event_id=?", (event["event_id"],)).fetchone()
            if prior:
                if prior[0] != wire:
                    raise ValueError("A work-team event ID was reused with different content.")
                return False
            for parent in event["parents"]:
                row = self._db.execute("SELECT workspace_id,kind,entity_id,wire FROM events WHERE event_id=?", (parent,)).fetchone()
                if row and (row[0], row[1], row[2]) != (event["workspace_id"], event["kind"], event["entity_id"]):
                    raise ValueError("A work-team revision refers to another record.")
                if row and event["event_id"] in json.loads(row[3])["parents"]:
                    raise ValueError("Work-team revisions contain a cycle.")
            # Also check children previously received before their parents.
            for row in self._db.execute("SELECT e.kind,e.entity_id,e.wire FROM events e JOIN event_parents p ON p.event_id=e.event_id WHERE e.workspace_id=? AND p.parent_id=?", (self._workspace(), event["event_id"])):
                child = json.loads(row[2])
                if event["event_id"] in child["parents"] and (row[0], row[1]) != (event["kind"], event["entity_id"]):
                    raise ValueError("A work-team revision refers to another record.")
            self._db.execute("INSERT INTO events VALUES(?,?,?,?,?,0,1)", (event["event_id"], event["workspace_id"], event["kind"], event["entity_id"], wire))
            self._db.executemany("INSERT INTO event_parents VALUES(?,?)", ((event["event_id"], parent) for parent in event["parents"]))
            # Detect cycles of any length, including parents received later.
            graph = {e["event_id"]: e["parents"] for e in self._events(event["kind"], event["entity_id"])}
            active, visited = set(), set()
            for identifier in graph:
                pending = [(identifier, False)]
                while pending:
                    item, leaving = pending.pop()
                    if leaving:
                        active.remove(item)
                        visited.add(item)
                    elif item in active:
                        raise ValueError("Work-team revisions contain a cycle.")
                    elif item in graph and item not in visited:
                        active.add(item)
                        pending.append((item, True))
                        pending.extend((parent, False) for parent in graph[item])
            self._view(event["kind"], event["entity_id"])
            if event["kind"] in {"note", "message"}:
                self._scope_cache.clear()
        return True

    def pending_events(self):
        with self._lock:
            return [json.loads(row[0]) for row in self._db.execute("SELECT wire FROM events WHERE workspace_id=? AND local=1 AND published=0 ORDER BY rowid", (self._workspace(),))]

    def mark_published(self, event_id):
        with self._lock, self._db:
            self._db.execute("UPDATE events SET published=1 WHERE event_id=? AND workspace_id=? AND local=1", (event_id, self._workspace()))

    def roster(self):
        return [{"id": entity["id"], "name": entity["payload"]["name"]} for entity in self.entities("profile") if not entity["deleted"]]

    @staticmethod
    def _receipt_id(employee_id, kind, entity_id):
        return hashlib.sha256(f"{employee_id}:{kind}:{entity_id}".encode()).hexdigest()[:32]

    @staticmethod
    def _schedule_signature(payload):
        return _json({"due_at": payload.get("due_at"), "order_due": payload.get("order_due"),
                      "repeat_minutes": payload.get("repeat_minutes", 30), "recipients": sorted(set(payload.get("recipients", []))),
                      "completed": bool(payload.get("done", False) or payload.get("kind") == "order" and payload.get("order_status") in {"delivered", "cancelled", "canceled"}),
                      "notify": payload.get("notify", True)})

    def _schedule_scope(self, entity):
        key = (self._workspace(), entity["kind"], entity["id"], tuple(entity["revisions"]))
        if key in self._scope_cache:
            return self._scope_cache[key]
        graph = {event["event_id"]: event for event in self._events(entity["kind"], entity["id"])}
        signature = self._schedule_signature(entity["payload"])
        pending, seen, boundaries = [entity["winner_revision"]], set(), set()
        while pending:
            identifier = pending.pop()
            if identifier in seen:
                continue
            seen.add(identifier)
            event = graph[identifier]
            matching = [parent for parent in event["parents"] if parent in graph and graph[parent]["operation"] == "put"
                        and self._schedule_signature(graph[parent]["payload"]) == signature]
            if matching:
                pending.extend(matching)
            else:
                boundaries.add(identifier)
        result = hashlib.sha256(_json(sorted(boundaries)).encode()).hexdigest()
        if len(self._scope_cache) >= 4000:
            self._scope_cache.clear()
        self._scope_cache[key] = result
        return result

    def receipt(self, employee_id, kind, entity_id):
        if not _identifier(employee_id) or kind not in {"note", "message"} or not _identifier(entity_id):
            raise ValueError("Invalid employee receipt.")
        with self._lock:
            events = self._events("receipt", self._receipt_id(employee_id, kind, entity_id))
            entity = self._view(kind, entity_id)
            scope = self._schedule_scope(entity) if entity else None
        actions = [e for e in events if e["operation"] == "put" and e["payload"].get("scope") == scope]
        presented = [e for e in actions if e["payload"]["action"] == "presented"]
        schedule = [e for e in actions if e["payload"]["action"] in {"presented", "snooze"}]
        graph = {event["event_id"]: event["parents"] for event in actions}
        ancestors = set()
        for event in schedule:
            pending = list(event["parents"])
            seen = set()
            while pending:
                parent = pending.pop()
                if parent in seen:
                    continue
                seen.add(parent)
                ancestors.add(parent)
                pending.extend(graph.get(parent, []))
        # Ignore clock skew for causally ordered actions. Concurrent snoozes
        # conservatively choose the later next-due time.
        leaves = [event for event in schedule if event["event_id"] not in ancestors]
        latest = max(leaves, key=lambda e: (e["payload"].get("next_due") or 0, e["event_id"]), default=None)
        return {"read": any(e["payload"]["action"] in {"read", "acknowledge"} for e in actions),
                "acknowledged": any(e["payload"]["action"] == "acknowledge" for e in actions),
                "presented": {e["payload"]["occurrence_id"] for e in presented},
                "has_schedule": latest is not None, "next_due": latest["payload"].get("next_due") if latest else None,
                "schedule_revision": latest["event_id"] if latest else "", "scope": scope,
                "latest_schedule_action": latest['payload']['action'] if latest else None,
                "ever_presented": any(e["payload"]["action"] == "presented" for e in events)}

    @staticmethod
    def _targeted(entity, employee_id):
        recipients = entity["payload"].get("recipients", [])
        return not recipients or employee_id in recipients

    @staticmethod
    def _completed(entity):
        payload = entity["payload"]
        return entity["deleted"] or payload.get("done", False) or payload.get("kind") == "order" and payload.get("order_status") in {"delivered", "cancelled", "canceled"}

    def inbox(self, employee_id=None):
        employee_id = employee_id or self.identity()["employee_id"]
        result = []
        names = {r["id"]: r["name"] for r in self.roster()}
        for entity in self.entities("message"):
            if entity["deleted"] or not self._targeted(entity, employee_id):
                continue
            receipt = self.receipt(employee_id, "message", entity["id"])
            result.append({**entity["payload"], "id": entity["id"], "entity_id": entity["id"], "entity_kind": "message",
                           "author_id": entity["author_id"], "author_name": names.get(entity["author_id"], "Employee"),
                           "read": receipt["read"], "acknowledged": receipt["acknowledged"],
                           "next_due": receipt["next_due"] if receipt["has_schedule"] else self._first_due(entity)})
        return sorted(result, key=lambda r: (r.get("created", 0), r["id"]), reverse=True)

    def _first_due(self, entity):
        payload = entity["payload"]
        due = payload.get("due_at")
        if due is None and payload.get("kind") == "order":
            due = payload.get("order_due")
        repeat = payload.get("repeat_minutes", 30)
        return due if due is not None else payload.get("created", entity["created"]) + repeat * 60 if repeat else None

    def due_notifications(self, employee_id, now=None):
        now = time.time() if now is None else now
        if not _identifier(employee_id) or not _timestamp(now):
            raise ValueError("Invalid notification employee or date.")
        result = []
        for kind in ("message", "note"):
            for entity in self.entities(kind):
                if self._completed(entity) or not self._targeted(entity, employee_id):
                    continue
                payload = entity["payload"]
                if payload.get("notify") is False:
                    continue
                if kind == "note" and payload.get("document") and payload.get("due_at") is None and not payload.get("repeat_minutes", 0):
                    continue
                receipt = self.receipt(employee_id, kind, entity["id"])
                if receipt["acknowledged"]:
                    continue
                initial = hashlib.sha256(f"{self._workspace()}:{employee_id}:{kind}:{entity['id']}:{receipt['scope']}:initial".encode()).hexdigest()
                due = self._first_due(entity)
                if due is None and not payload.get("repeat_minutes", 30) and receipt["ever_presented"]:
                    continue
                if initial not in receipt["presented"]:
                    if receipt["has_schedule"] and receipt["next_due"] is not None and receipt["next_due"] > now:
                        continue
                    occurrence, notification_kind = initial, "added"
                else:
                    due = receipt["next_due"] if receipt["has_schedule"] else due
                    if due is None or due > now:
                        continue
                    occurrence = hashlib.sha256(f"{self._workspace()}:{employee_id}:{kind}:{entity['id']}:{receipt['scope']}:{due}:{receipt['schedule_revision']}".encode()).hexdigest()
                    notification_kind = "reminder"
                    if occurrence in receipt["presented"]:
                        continue
                result.append({"id": entity["id"], "entity_id": entity["id"], "entity_kind": kind,
                    "occurrence_id": occurrence, "notification_kind": notification_kind,
                    "title": payload.get("title", "Message"), "body": payload.get("body", ""),
                    "customer": payload.get("customer", ""), "note_id": payload.get("note_id", entity["id"] if kind == "note" else ""),
                    "repeat_minutes": payload.get("repeat_minutes", 30), "due": due, "author_id": entity["author_id"],
                    "order_ref": payload.get("order_ref", ""), "order_status": payload.get("order_status", ""),
                    "checklist": payload.get("checklist", [])})
        return sorted(result, key=lambda n: (n["notification_kind"] != "added", n["due"] if n["due"] is not None else float("inf"), n["entity_id"]))

    def _receipt_action(self, employee_id, kind, entity_id, action, now, **values):
        if employee_id != self.identity()["employee_id"]:
            raise ValueError("An employee may only update their own receipts.")
        entity = self.get_entity(kind, entity_id)
        if entity is None or not self._targeted(entity, employee_id):
            raise ValueError("This reminder is not assigned to you.")
        if not _timestamp(now):
            raise ValueError("Invalid receipt date.")
        payload = {"employee_id": employee_id, "entity_kind": kind, "entity_id": entity_id, "action": action, "at": now,
                   "scope": self._schedule_scope(entity), **values}
        return self._put("receipt", self._receipt_id(employee_id, kind, entity_id), payload, now=now)

    def mark_presented(self, employee_id, entity_kind, entity_id, occurrence_id, now=None):
        now = time.time() if now is None else now
        with self._lock:
            candidate = next((n for n in self.due_notifications(employee_id, now) if n["entity_kind"] == entity_kind and n["entity_id"] == entity_id and n["occurrence_id"] == occurrence_id), None)
            if candidate is None:
                return False
            repeat = candidate["repeat_minutes"]
            next_due = now + repeat * 60 if repeat else None
            if candidate["notification_kind"] == "added" and candidate["due"] is not None and candidate["due"] > now:
                next_due = candidate["due"]
            self._receipt_action(employee_id, entity_kind, entity_id, "presented", now, occurrence_id=occurrence_id, next_due=next_due)
        return True

    def mark_read(self, employee_id, entity_kind, entity_id, now=None):
        return self._receipt_action(employee_id, entity_kind, entity_id, "read", time.time() if now is None else now)

    def snooze(self, employee_id, entity_kind, entity_id, minutes=5, now=None):
        if type(minutes) is not int or not 1 <= minutes <= 10080:
            raise ValueError("Choose a snooze between 1 minute and 7 days.")
        now = time.time() if now is None else now
        return self._receipt_action(employee_id, entity_kind, entity_id, "snooze", now, next_due=now + minutes * 60)

    def acknowledge(self, employee_id, entity_kind, entity_id, now=None):
        return self._receipt_action(employee_id, entity_kind, entity_id, "acknowledge", time.time() if now is None else now)
