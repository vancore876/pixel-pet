"""Durable notes, per-note scheduling, and an explicitly linked text file."""
from __future__ import annotations

from collections import Counter
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import time
import uuid
from PySide6.QtCore import QObject, QTimer, Signal, QFileSystemWatcher

MAX_NOTES = 2000
ORDER_STATUSES = ('new', 'preparing', 'ready', 'delivered', 'cancelled')
BUSINESS_FIELDS = ('kind', 'customer', 'contact', 'order_ref', 'order_status', 'order_due', 'checklist')


def clean_text(value, limit):
    return value.strip()[:limit] if isinstance(value, str) else ""


def note_fingerprint(note):
    return hashlib.sha256(json.dumps([note.get("title", ""), note.get("body", ""),
                                     {key: note.get(key) for key in BUSINESS_FIELDS}], sort_keys=True,
                                    ensure_ascii=False).encode("utf-8")).hexdigest()


def business_details(note):
    return {key: note.get(key) for key in BUSINESS_FIELDS}


def pending_items(note):
    return [item for item in note.get('checklist', []) if not item['done']]


def note_search_text(note):
    return ' '.join([note['title'], note['body'], note.get('customer', ''), note.get('contact', ''),
                     note.get('order_ref', ''), *[i['text'] for i in note.get('checklist', [])]])


def business_summary(notes, now=None):
    now = time.time() if now is None else now
    active = [n for n in notes if not n['done']]
    orders = [n for n in active if n.get('kind') == 'order']
    return {'active_notes': len(active), 'open_orders': len(orders),
            'ready_orders': sum(n['order_status'] == 'ready' for n in orders),
            'late_orders': sum(bool(n['order_due'] and n['order_due'] < now) for n in orders),
            'unchecked_items': sum(len(pending_items(n)) for n in active)}


def fallback_reminder(note, kind='reminder'):
    import random
    label = note.get('order_ref') or note['title']
    customer = (' for ' + note['customer']) if note.get('customer') else ''
    items = pending_items(note)
    task = f"{items[0]['quantity']} × {items[0]['text']}" if items else note['body'].splitlines()[0][:100] if note['body'] else label
    if note.get('kind') == 'order':
        if note.get('order_status') == 'ready':
            choices = [f"{label}{customer} is marked ready. Keep the pickup details handy.",
                       f"Ready order: {label}{customer}. Check the arranged handover time.",
                       f"Your ready list includes {label}{customer}. Review it before handover."]
        else:
            choices = [f"Check {label}{customer}. Next item: {task}.",
                       f"A little order check: {label}{customer} still has {len(items)} unchecked items.",
                       f"Keep {label}{customer} moving. Review {task}.",
                       f"Order reminder: {label}{customer}. Is {task} sorted yet?"]
    else:
        choices = [f"A quick nudge for {label}: {task}.", f"When you have a moment, check {label}.",
                   f"Next on your list: {task}.", f"Keeping {label} on your radar. What is the next small step?"]
    if kind == 'added':
        choices = [f"Saved {label}{customer}. I’ll keep the remaining items in view.", f"Got it—{label}{customer} is on your list.", *choices]
    recent = {text.casefold() for text in note.get('reminder_history', [])[-5:]}
    available = [s for s in choices if s.casefold() not in recent]
    return random.choice(available or choices)[:280]


def current_guidance(note):
    guidance = note.get("ai_guidance") or {}
    return guidance if guidance.get("fingerprint") == note_fingerprint(note) else {}


def validate_guidance(value):
    if not isinstance(value, dict):
        return {}
    fingerprint = clean_text(value.get("fingerprint"), 64)
    if len(fingerprint) != 64 or any(c not in "0123456789abcdef" for c in fingerprint):
        return {}
    def timestamp(key):
        v = value.get(key)
        return float(v) if type(v) in (int, float) and math.isfinite(v) and v > 0 else None
    reminder = clean_text(value.get("reminder"), 280)
    if not reminder:
        return {}
    return {"fingerprint": fingerprint, "reminder": reminder,
            "next_step": clean_text(value.get("next_step"), 180),
            "suggested_due": timestamp("suggested_due"), "reason": clean_text(value.get("reason"), 180),
            "generated": timestamp("generated")}


class NoteStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.state = {"version": 2, "notes": [], "linked_file": "", "linked_lines": []}
        self.warning = ""
        self.blocked_write = False
        if self.path.exists():
            try:
                if self.path.stat().st_size > 128 * 1024 * 1024:
                    raise ValueError("Notes file too large")
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict) or not isinstance(raw.get("notes"), list):
                    raise ValueError("Invalid notes file")
                seen = set()
                for data in raw["notes"][:MAX_NOTES]:
                    note = self.validate_note(data)
                    if note and note["id"] not in seen:
                        self.state["notes"].append(note)
                        seen.add(note["id"])
                linked = clean_text(raw.get("linked_file"), 4096)
                self.state["linked_file"] = linked
                lines = raw.get("linked_lines", [])
                self.state["linked_lines"] = [clean_text(line, 10000) for line in lines[:MAX_NOTES] if isinstance(line, str)] if isinstance(lines, list) else []
            except (OSError, ValueError, UnicodeError):
                self.warning = "Notes could not be read; the original was backed up."
                try:
                    self.path.replace(self.path.with_suffix(".corrupt.json"))
                except OSError:
                    self.warning = "Notes could not be read or backed up. Check file permissions."
                    self.blocked_write = True

    @staticmethod
    def validate_note(data):
        if not isinstance(data, dict):
            return None
        identifier = clean_text(data.get("id"), 64)
        body = clean_text(data.get("body"), 10000)
        title = clean_text(data.get("title"), 100)
        if not title and body:
            title = body.splitlines()[0][:80]
        if not identifier or not (body or title):
            return None
        def timestamp(key):
            value = data.get(key)
            return float(value) if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None
        repeat = data.get("repeat_minutes", 30)
        repeat = max(0, min(1440, repeat)) if type(repeat) is int else 30
        pos = data.get("pin_position")
        if not isinstance(pos, list) or len(pos) != 2 or not all(type(x) is int and abs(x) < 100000 for x in pos):
            pos = None
        created = timestamp("created") or time.time()
        kind = data.get('kind') if data.get('kind') in ('note', 'list', 'order') else 'note'
        status = data.get('order_status') if data.get('order_status') in ORDER_STATUSES else 'new'
        checklist, item_ids = [], set()
        for row in (data.get('checklist') if isinstance(data.get('checklist'), list) else [])[:100]:
            if not isinstance(row, dict) or not clean_text(row.get('text'), 200):
                continue
            item_id = clean_text(row.get('id'), 64) or uuid.uuid4().hex
            if item_id in item_ids:
                continue
            item_ids.add(item_id)
            qty = row.get('quantity', 1)
            checklist.append({'id': item_id, 'text': clean_text(row.get('text'), 200),
                              'quantity': max(1, min(9999, qty)) if type(qty) is int else 1, 'done': row.get('done') is True})
        done = data.get('done') is True or kind == 'order' and status in ('delivered', 'cancelled')
        if kind == 'order' and done and status not in ('delivered', 'cancelled'):
            status = 'delivered'
        return {"id": identifier, "title": title, "body": body, "created": created,
                "updated": timestamp("updated") or created,
                'written': timestamp('written') or timestamp('updated') or created,
                "next_due": timestamp("next_due"), "repeat_minutes": repeat,
                "done": done, "pinned": data.get("pinned") is True,
                "pin_position": pos, "unannounced": data.get("unannounced") is True,
                "source": clean_text(data.get("source"), 4096),
                "ai_guidance": validate_guidance(data.get("ai_guidance")),
                "kind": kind, "customer": clean_text(data.get('customer'), 100), 'contact': clean_text(data.get('contact'), 100),
                'order_ref': clean_text(data.get('order_ref'), 80), 'order_status': status, 'order_due': timestamp('order_due'),
                'checklist': checklist, 'reminder_history': [clean_text(t, 280) for t in data.get('reminder_history', [])[-5:] if isinstance(t, str)] if isinstance(data.get('reminder_history'), list) else []}

    @property
    def notes(self):
        return self.state["notes"]

    def find(self, identifier):
        return next((n for n in self.notes if n["id"] == identifier), None)

    def save(self):
        if self.blocked_write:
            raise OSError("The original notebook could not be backed up; it has been left intact.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(temp, self.path)

    def transaction(self, change):
        previous = copy.deepcopy(self.state)
        try:
            result = change()
            self.save()
            return result
        except Exception:
            self.state = previous
            raise

    def new_note(self, title, body, repeat, due, now, source="", details=None):
        if len(self.notes) >= MAX_NOTES:
            raise ValueError("Your notebook is full. Export or remove older notes before adding more.")
        if not clean_text(title, 100) and not clean_text(body, 10000):
            raise ValueError("Write a note before saving it.")
        return self.validate_note({"id": uuid.uuid4().hex, "title": title, "body": body, "created": now,
            "next_due": due if due is not None else now + repeat * 60 if repeat else None,
            "repeat_minutes": repeat, "done": False, "pinned": False, "unannounced": True, "source": source, **(details or {})})

    def add(self, title, body, repeat=30, due=None, now=None, source="", details=None):
        now = time.time() if now is None else now
        note = self.new_note(title, body, repeat, due, now, source, details)
        def change():
            self.notes.append(note)
            return note
        return self.transaction(change)

    def edit(self, identifier, title, body, repeat, due=None, now=None, details=None):
        now = time.time() if now is None else now
        note = self.find(identifier)
        if not note:
            raise ValueError("This note no longer exists.")
        updated = dict(note, title=title, body=body, repeat_minutes=repeat,
                       next_due=due if due is not None else now + repeat * 60 if repeat else None, **(details or {}))
        updated = self.validate_note(updated)
        if not updated:
            raise ValueError("Write a note before saving it.")
        updated['updated'] = now
        if (updated['title'], updated['body']) != (note['title'], note['body']):
            updated['written'] = now
        def change():
            note.update(updated)
            return note
        return self.transaction(change)

    def modify(self, identifier, **changes):
        note = self.find(identifier)
        if not note:
            return None
        def change():
            updated = self.validate_note(dict(note, **changes))
            if not updated:
                raise ValueError("Invalid note")
            if set(changes) - {'ai_guidance', 'reminder_history'}:
                updated['updated'] = time.time()
            if (updated['title'], updated['body']) != (note['title'], note['body']):
                updated['written'] = time.time()
            note.update(updated)
            return note
        return self.transaction(change)

    def next_notification(self, now):
        active = [n for n in self.notes if not n["done"]]
        pending = [n for n in active if n["unannounced"]]
        if pending:
            return pending[0], "added"
        due = sorted((n for n in active if n["next_due"] is not None and n["next_due"] <= now), key=lambda n: n["next_due"])
        return (due[0], "reminder") if due else (None, "")

    def acknowledge(self, identifier, kind, now):
        note = self.find(identifier)
        if not note:
            return
        changes = {"unannounced": False}
        if kind == "reminder" or note["next_due"] is not None and note["next_due"] <= now:
            changes["next_due"] = now + note["repeat_minutes"] * 60 if note["repeat_minutes"] else None
        self.modify(identifier, **changes)

    def snooze(self, identifier, minutes=5, now=None):
        return self.modify(identifier, next_due=(time.time() if now is None else now) + minutes * 60, unannounced=False)

    def complete(self, identifier, done=True):
        note = self.find(identifier)
        repeat = note["repeat_minutes"] if note else 0
        status = ('delivered' if done else 'new') if note and note['kind'] == 'order' else note['order_status'] if note else 'new'
        return self.modify(identifier, done=done, order_status=status, unannounced=False,
                           next_due=None if done or not repeat else time.time() + repeat * 60)

    def delete(self, identifier):
        def change():
            self.state["notes"] = [n for n in self.notes if n["id"] != identifier]
        self.transaction(change)

    def import_backup(self, payload):
        if not isinstance(payload, dict) or not isinstance(payload.get('notes'), list) or len(payload['notes']) > MAX_NOTES:
            raise ValueError('Choose a Jeffery notebook backup with up to 2,000 notes.')
        incoming = {}
        for data in payload['notes']:
            note = self.validate_note(data)
            if not note:
                raise ValueError('The backup contains an invalid note. Nothing was imported.')
            note['source'] = ''
            note['pinned'] = False
            note['pin_position'] = None
            if note['id'] not in incoming or note['updated'] > incoming[note['id']]['updated']:
                incoming[note['id']] = note
        existing = {n['id']: n for n in self.notes}
        if len(set(existing) | set(incoming)) > MAX_NOTES:
            raise ValueError('Import would exceed the 2,000-note notebook limit.')
        def change():
            count = 0
            for identifier, note in incoming.items():
                current = existing.get(identifier)
                if current is None:
                    note['unannounced'] = not note['done']
                    self.notes.append(note)
                    count += 1
                elif note['updated'] > current['updated']:
                    current.update({**note, 'pinned': current['pinned'], 'pin_position': current['pin_position'], 'source': current['source']})
                    count += 1
            return count
        return self.transaction(change)

    def link_file(self, path):
        path = Path(path).expanduser().resolve()
        if path.suffix.lower() != ".txt" or not path.is_file():
            raise ValueError("Choose an existing .txt file.")
        if self.state["linked_file"] == str(path):
            return
        def change():
            self.state["linked_file"] = str(path)
            self.state["linked_lines"] = []
        self.transaction(change)

    def unlink_file(self):
        def change():
            self.state["linked_file"], self.state["linked_lines"] = "", []
        self.transaction(change)

    def import_text(self, text, repeat=30, now=None):
        now = time.time() if now is None else now
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if any(len(line) > 10000 for line in lines):
            raise ValueError("Each linked note must be at most 10,000 characters.")
        if len(lines) > MAX_NOTES:
            raise ValueError("Linked files support up to 2,000 nonblank lines.")
        old = Counter(self.state["linked_lines"])
        additions = []
        for line in lines:
            if old[line]:
                old[line] -= 1
            else:
                additions.append(line)
        if len(self.notes) + len(additions) > MAX_NOTES:
            raise ValueError("Your notebook is full; new linked lines could not be imported.")
        def change():
            created = []
            for line in additions:
                note = self.new_note(line[:80], line, repeat, None, now, self.state["linked_file"])
                self.notes.append(note)
                created.append(note)
            self.state["linked_lines"] = lines
            return created
        if lines == self.state["linked_lines"]:
            return []
        return self.transaction(change)


class NoteService(QObject):
    changed = Signal(str)
    notification = Signal(object, str)
    error = Signal(str)

    def __init__(self, store, settings, can_notify=lambda: True, parent=None):
        super().__init__(parent)
        self.store, self.settings, self.can_notify = store, settings, can_notify
        self.last_delivery = -float("inf")
        self.signature = None
        self.last_file_error = ""
        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(lambda _: self.scan_linked(force=True))
        self.watcher.directoryChanged.connect(lambda _: self.scan_linked())
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.tick)
        self.scan_timer = QTimer(self)
        self.scan_timer.setInterval(5000)
        self.scan_timer.timeout.connect(self.scan_linked)

    def start(self):
        self.install_watch()
        self.scan_linked()
        self.timer.start()
        self.scan_timer.start()

    def stop(self):
        self.timer.stop()
        self.scan_timer.stop()
        paths = self.watcher.files() + self.watcher.directories()
        if paths:
            self.watcher.removePaths(paths)

    def install_watch(self):
        paths = self.watcher.files() + self.watcher.directories()
        if paths:
            self.watcher.removePaths(paths)
        linked = self.store.state["linked_file"]
        if linked:
            path = Path(linked)
            add = [str(p) for p in (path, path.parent) if p.exists()]
            if add:
                self.watcher.addPaths(add)

    def scan_linked(self, force=False):
        linked = self.store.state["linked_file"]
        if not linked:
            return
        try:
            path = Path(linked)
            stat = path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
            if not force and signature == self.signature:
                return
            if stat.st_size > 1024 * 1024:
                raise ValueError("The linked text file must be smaller than 1 MB.")
            payload = path.read_bytes()
            text = payload.decode("utf-16") if payload.startswith((b'\xff\xfe', b'\xfe\xff')) else payload.decode("utf-8-sig")
            added = self.store.import_text(text, self.settings["note_repeat_minutes"])
            self.signature = signature
            self.last_file_error = ""
            self.install_watch()
            if added:
                self.changed.emit("")
        except (OSError, ValueError, UnicodeError) as exc:
            # Keep the baseline intact while a file is replaced or temporarily absent.
            message = f"Linked notepad: {exc}"
            if message != self.last_file_error:
                self.last_file_error = message
                self.error.emit(message)

    def link(self, path):
        self.store.link_file(path)
        self.signature = None
        self.install_watch()
        self.scan_linked(force=True)
        self.changed.emit("")

    def unlink(self):
        self.store.unlink_file()
        self.signature = None
        self.install_watch()
        self.changed.emit("")

    def save_note(self, identifier, title, body, repeat, due=None, details=None):
        note = self.store.edit(identifier, title, body, repeat, due, details=details) if identifier else self.store.add(title, body, repeat, due, details=details)
        self.changed.emit(note["id"])
        return note

    def modify(self, identifier, **changes):
        note = self.store.modify(identifier, **changes)
        self.changed.emit(identifier)
        return note

    def complete(self, identifier, done=True):
        self.store.complete(identifier, done)
        self.changed.emit(identifier)

    def snooze(self, identifier, minutes=5):
        self.store.snooze(identifier, minutes)
        self.changed.emit(identifier)

    def delete(self, identifier):
        self.store.delete(identifier)
        self.changed.emit(identifier)

    def tick(self):
        if not self.settings["note_reminders"] or self.settings["quiet_mode"] or not self.can_notify() or time.monotonic() - self.last_delivery < 9:
            return
        note, kind = self.store.next_notification(time.time())
        if note:
            try:
                self.store.acknowledge(note["id"], kind, time.time())
                self.last_delivery = time.monotonic()
                self.notification.emit(dict(note), kind)
                self.changed.emit(note["id"])
            except (OSError, ValueError) as exc:
                self.error.emit(f"Could not save a reminder: {exc}")
