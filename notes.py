"""Durable notes, per-note scheduling, and an explicitly linked text file."""
from __future__ import annotations

from collections import Counter
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import shutil
import tempfile
import time
import uuid
from PySide6.QtCore import QObject, QTimer, Signal, QFileSystemWatcher, QLockFile

from fuzzy_search import search_words, typo_score
from memory import contains_secret
from auto_parts import MAX_PAYMENT_CENTS, MAX_UNIT_PRICE_CENTS, STOCK_STATUSES, dashboard_summary, validated_cents

MAX_NOTES = 2000
MAX_DOCUMENT_NOTES = 200
MAX_DOCUMENT_CHARACTERS = 1_000_000_000
MAX_INLINE_CHARACTERS = 10_000
ORDER_STATUSES = ('new', 'preparing', 'ready', 'delivered', 'cancelled')
BUSINESS_FIELDS = ('kind', 'customer', 'contact', 'order_ref', 'order_status', 'order_due',
                   'vehicle', 'registration', 'vin', 'priority', 'order_type', 'currency',
                   'payment_received_cents', 'checklist')
TEAM_FIELDS = ('team_id', 'team_workspace_id', 'team_revisions', 'team_recipients', 'team_due_at', 'team_conflict_revision', 'team_notify')


def validate_team_metadata(data):
    """Retain portable shared-note references without copying an employee profile.

    Device identities, delivery receipts and snoozes live in the per-user team
    cache. A notebook backup may carry these references but never impersonates
    the employee who originally created it.
    """
    if not any(key in data for key in TEAM_FIELDS):
        return {'team_shared': True} if data.get('team_shared') is True else {}
    def identifier(value):
        return (value if isinstance(value, str) and 0 < len(value) <= 128
                and all(c.isascii() and (c.isalnum() or c in '-_.') for c in value) else '')
    revisions = data.get('team_revisions', [])
    recipients = data.get('team_recipients', [])
    if not isinstance(revisions, list) or len(revisions) > 2000 or any(not identifier(v) for v in revisions):
        raise ValueError('The shared note contains invalid revision metadata.')
    if isinstance(recipients, list) and '*' in recipients:
        recipients = []
    if not isinstance(recipients, list) or len(recipients) > 2000 or any(not identifier(v) for v in recipients):
        raise ValueError('Choose valid employees for this shared note.')
    due = data.get('team_due_at')
    if due is not None and (type(due) not in (int, float) or not math.isfinite(due) or due < 0):
        raise ValueError('The shared note contains an invalid reminder date.')
    for key in ('team_id', 'team_workspace_id'):
        if data.get(key) and not identifier(data[key]):
            raise ValueError('The shared note contains an invalid workspace reference.')
    return {'team_id': identifier(data.get('team_id')), 'team_workspace_id': identifier(data.get('team_workspace_id')),
            'team_revisions': list(dict.fromkeys(revisions)), 'team_recipients': list(dict.fromkeys(recipients)),
            'team_due_at': float(due) if due is not None else None,
            'team_conflict_revision': identifier(data.get('team_conflict_revision')),
            'team_notify': data.get('team_notify') is not False,
            **({'team_shared': True} if data.get('team_shared') is True or data.get('team_id') else {})}


def portable_notebook_state(state):
    """Small-note mobile exports retain privacy without employee references."""
    portable = copy.deepcopy(state)
    portable['linked_file'] = ''
    portable['linked_lines'] = []
    for note in portable.get('notes', []):
        shared = note.get('team_shared') is True or bool(note.get('team_id') or note.get('team_workspace_id')
            or note.get('team_revisions') or note.get('team_recipients') or note.get('team_due_at') is not None)
        for key in list(note):
            if key.startswith('team_'):
                note.pop(key)
        note['source'] = ''
        if shared:
            note['team_shared'] = True
    return portable


def clean_text(value, limit):
    return value.strip()[:limit] if isinstance(value, str) else ""


def note_fingerprint(note):
    return hashlib.sha256(json.dumps([note.get("title", ""), note.get("body", ""),
                                     note.get("body_sha256", ""),
                                     {key: note.get(key) for key in BUSINESS_FIELDS}], sort_keys=True,
                                    ensure_ascii=False).encode("utf-8")).hexdigest()


def business_details(note):
    return {key: note.get(key) for key in BUSINESS_FIELDS}


def pending_items(note):
    return [item for item in note.get('checklist', []) if not item['done']]


def note_search_text(note):
    return ' '.join([note['title'], note['body'], note.get('customer', ''), note.get('contact', ''),
                     note.get('order_ref', ''), note.get('order_status', ''), note.get('vehicle', ''),
                     note.get('registration', ''), note.get('vin', ''), note.get('priority', ''),
                     note.get('order_type', ''), note.get('currency', ''),
                     *[' '.join([i['text'], i.get('part_number', ''), i.get('supplier', ''),
                                 i.get('bin_location', ''), i.get('stock_status', '')])
                       for i in note.get('checklist', [])]])


def note_search_label(note):
    """Keep approximate matching on bounded labels, never complete note bodies."""
    label = ' '.join([note['title'], note.get('customer', ''), note.get('order_ref', ''),
                      note.get('vehicle', ''), note.get('registration', ''),
                      note.get('document_source', '')])[:600]
    return '' if contains_secret(label) else label


def business_summary(notes, now=None):
    now = time.time() if now is None else now
    active = [n for n in notes if not n['done']]
    return {'active_notes': len(active), **dashboard_summary(notes, now),
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
        self._corrupt_backed_up = False
        self._disk_digest = self._file_digest()
        self._documents = None
        self._restore_recovery_pending = False
        self._restore_recovery_allowed = True
        if self.path.exists():
            try:
                if self.path.stat().st_size > 128 * 1024 * 1024:
                    raise ValueError("Notes file too large")
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict) or not isinstance(raw.get("notes"), list):
                    raise ValueError("Invalid notes file")
                if (type(raw.get('version', 2)) is not int or raw.get('version', 2) not in (1, 2)
                        or len(raw['notes']) > MAX_NOTES):
                    raise ValueError('Unsupported notes file')
                seen = set()
                for data in raw["notes"]:
                    note = self.validate_note(data)
                    if not note or note['id'] in seen:
                        raise ValueError('Invalid or duplicate note')
                    self.state["notes"].append(note)
                    seen.add(note["id"])
                linked = clean_text(raw.get("linked_file"), 4096)
                self.state["linked_file"] = linked
                lines = raw.get("linked_lines", [])
                self.state["linked_lines"] = [clean_text(line, 10000) for line in lines[:MAX_NOTES] if isinstance(line, str)] if isinstance(lines, list) else []
                if any(note.get('document_id') for note in self.notes) and not (self.path.parent / 'documents.sqlite').is_file():
                    self.warning = 'Full document text is missing. Keep documents.sqlite with notes.json or restore a complete ZIP backup.'
                self._restore_recovery_pending = True
            except (OSError, ValueError, UnicodeError):
                self._restore_recovery_allowed = False
                # A later damaged row must not leave a writable, partial notebook.
                self.state = {"version": 2, "notes": [], "linked_file": "", "linked_lines": []}
                self.warning = "Notes could not be read; the original was backed up."
                try:
                    backup = self.path.with_suffix('.corrupt.json')
                    if backup.exists():
                        backup = self.path.with_suffix(f'.corrupt-{uuid.uuid4().hex}.json')
                    shutil.copy2(self.path, backup)
                    self._corrupt_backed_up = True
                    self.blocked_write = True
                    self.warning = "Notes could not be read. The original and a recovery copy were preserved; restore a notebook backup before saving."
                except OSError:
                    self.warning = "Notes could not be read or backed up. Check file permissions."
                    self.blocked_write = True

    @staticmethod
    def validate_note(data):
        if not isinstance(data, dict):
            return None
        if isinstance(data.get('body'), str) and len(data['body'].strip()) > MAX_INLINE_CHARACTERS:
            raise ValueError('The notebook contains oversized previews. Use a complete document backup to preserve its full text.')
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
                              'quantity': max(1, min(9999, qty)) if type(qty) is int else 1, 'done': row.get('done') is True,
                              'part_number': clean_text(row.get('part_number'), 80),
                              'supplier': clean_text(row.get('supplier'), 100),
                              'bin_location': clean_text(row.get('bin_location'), 60),
                              'unit_price_cents': validated_cents(row.get('unit_price_cents'), MAX_UNIT_PRICE_CENTS),
                              'stock_status': row.get('stock_status') if row.get('stock_status') in STOCK_STATUSES else 'check_stock'})
        done = data.get('done') is True or kind == 'order' and status in ('delivered', 'cancelled')
        if kind == 'order' and done and status not in ('delivered', 'cancelled'):
            status = 'delivered'
        document = {}
        if any(key in data for key in ('document_id', 'body_characters', 'body_sha256')):
            document_id = data.get('document_id')
            characters = data.get('body_characters')
            digest = data.get('body_sha256')
            if (not isinstance(document_id, str) or len(document_id) != 32
                    or any(c not in '0123456789abcdef' for c in document_id)
                    or type(characters) is not int or not max(1, len(body)) <= characters <= MAX_DOCUMENT_CHARACTERS
                    or not isinstance(digest, str) or len(digest) != 64
                    or any(c not in '0123456789abcdef' for c in digest)):
                raise ValueError('The notebook contains invalid document metadata.')
            document = {'document_id': document_id, 'body_characters': characters, 'body_sha256': digest}
        return {"id": identifier, "title": title, "body": body, "created": created,
                "updated": timestamp("updated") or created,
                'written': timestamp('written') or timestamp('updated') or created,
                "next_due": timestamp("next_due"), "repeat_minutes": repeat,
                "done": done, "pinned": data.get("pinned") is True,
                "pin_position": pos, "unannounced": data.get("unannounced") is True,
                "source": clean_text(data.get("source"), 4096),
                "document_source": clean_text(data.get("document_source"), 300),
                "ai_guidance": validate_guidance(data.get("ai_guidance")),
                "kind": kind, "customer": clean_text(data.get('customer'), 100), 'contact': clean_text(data.get('contact'), 100),
                'order_ref': clean_text(data.get('order_ref'), 80), 'order_status': status, 'order_due': timestamp('order_due'),
                'vehicle': clean_text(data.get('vehicle'), 160), 'registration': clean_text(data.get('registration'), 32),
                'vin': clean_text(data.get('vin'), 32), 'priority': 'urgent' if data.get('priority') == 'urgent' else 'normal',
                'order_type': 'quote' if data.get('order_type') == 'quote' else 'order', 'currency': 'JMD',
                'payment_received_cents': validated_cents(data.get('payment_received_cents'), MAX_PAYMENT_CENTS),
                'checklist': checklist, 'reminder_history': [clean_text(t, 280) for t in data.get('reminder_history', [])[-5:] if isinstance(t, str)] if isinstance(data.get('reminder_history'), list) else [], **document,
                **validate_team_metadata(data)}

    @property
    def documents(self):
        """Open the indexed full-text store only when a document is needed."""
        if self._documents is None:
            from documents import DocumentStore
            self._documents = DocumentStore(self.path.parent / 'documents.sqlite')
        if self._restore_recovery_pending:
            try:
                self._documents.finish_restore(self._document_manifest(), self.inline_character_count(), recover=True)
            except (OSError, ValueError):
                self.warning = 'A prepared restore still needs storage access. Its original and restored text were preserved; try again when storage is available.'
            else:
                self._restore_recovery_pending = False
        return self._documents

    def _document_manifest(self):
        return {note['document_id']: (note['body_characters'], note['body_sha256'])
                for note in self.notes if note.get('document_id')}

    def _finish_committed_restore(self, *, recover=True):
        if self._documents is not None and (self._restore_recovery_allowed or not recover):
            try:
                self._documents.finish_restore(self._document_manifest(), self.inline_character_count(), recover=recover)
            except (OSError, ValueError):
                self._restore_recovery_pending = self._restore_recovery_allowed
                self.warning = 'The notebook was saved. Restored text remains readable while storage activation is retried on the next save or restart.'

    def inline_character_count(self):
        return sum(len(note['body']) for note in self.notes if not note.get('document_id'))

    def close(self):
        """Stop background document cleanup before the notebook files are released."""
        if self._documents is not None:
            return self._documents.close(timeout=0.5)
        return True

    def character_count(self):
        documents = {}
        for note in self.notes:
            if note.get('document_id'):
                documents[note['document_id']] = max(documents.get(note['document_id'], 0), note['body_characters'])
        return self.inline_character_count() + sum(documents.values())

    def _check_capacity(self):
        if self.character_count() > MAX_DOCUMENT_CHARACTERS:
            raise ValueError('Your notebook supports up to 1,000,000,000 characters. Remove older content before saving more.')

    def _discard_unreferenced(self, identifiers):
        referenced = {note.get('document_id') for note in self.notes}
        for identifier in set(identifiers) - referenced - {None, ''}:
            try:
                self.documents.delete(identifier)
            except (OSError, ValueError, sqlite3.Error):
                self.warning = 'A note was removed, but its unreferenced document text could not be cleaned up. Check storage permissions.'

    @staticmethod
    def _source_label(label):
        label = clean_text(label, 300)
        if label and not label.lower().startswith(('https://', 'http://')):
            label = label.replace('\\', '/').rsplit('/', 1)[-1]
        return label

    def _verified_document(self, prepared):
        if not isinstance(prepared, dict):
            raise ValueError('The prepared document is invalid.')
        identifier = prepared.get('document_id')
        if (not isinstance(identifier, str) or len(identifier) != 32
                or any(c not in '0123456789abcdef' for c in identifier)):
            raise ValueError('The prepared document is invalid.')
        actual = self.documents.metadata(identifier)
        if not actual or any(actual.get(key) != prepared.get(key) for key in ('body_characters', 'body_sha256')):
            raise ValueError('The document is missing or changed. Import it again before saving.')
        if type(actual.get('body_characters')) is not int or not 0 < actual['body_characters'] <= MAX_DOCUMENT_CHARACTERS:
            raise ValueError('The document exceeds the notebook character limit.')
        return {'document_id': identifier, 'body_characters': actual['body_characters'],
                'body_sha256': actual['body_sha256'], 'body': actual.get('body', '')[:MAX_INLINE_CHARACTERS]}

    def add_prepared_document(self, prepared, title, source_label='', now=None):
        """Attach already indexed text in one small, atomic notebook write."""
        now = time.time() if now is None else now
        identifier = prepared.get('document_id') if isinstance(prepared, dict) else None
        try:
            document = self._verified_document(prepared)
            note = self.new_note(title, document['body'], 0, None, now,
                                 details={**document, 'document_source': self._source_label(source_label),
                                          'unannounced': False})
            def change():
                self.notes.append(note)
                return note
            return self.transaction(change)
        except Exception:
            if isinstance(identifier, str) and len(identifier) == 32 and all(c in '0123456789abcdef' for c in identifier):
                self._discard_unreferenced([identifier])
            raise

    @property
    def notes(self):
        return self.state["notes"]

    def find(self, identifier):
        return next((n for n in self.notes if n["id"] == identifier), None)

    def _file_digest(self):
        try:
            return hashlib.sha256(self.path.read_bytes()).digest()
        except FileNotFoundError:
            return None

    def save(self):
        # Serialise the check and replacement across processes, including profiles
        # that accidentally point to the same notebook.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(self.path) + '.write.lock')
        if not lock.tryLock(100):
            raise OSError('The notebook is being saved elsewhere. Try again shortly.')
        try:
            self._save_locked()
        finally:
            lock.unlock()

    def _save_locked(self):
        if self.blocked_write:
            raise OSError("The notebook could not be read safely. Its original was preserved; restore a backup before saving.")
        if self._file_digest() != self._disk_digest:
            raise OSError('The notebook changed outside this window. Reopen Jeffery before saving; your draft is preserved.')
        self._check_capacity()
        if self._documents is not None and self._restore_recovery_allowed:
            self._documents.finish_restore(self._document_manifest(), self.inline_character_count(), validate_only=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                             prefix='.notes-', suffix='.tmp', delete=False) as output:
                temporary = Path(output.name)
                json.dump(self.state, output, indent=2, ensure_ascii=False, allow_nan=False)
                output.write('\n')
                output.flush()
                os.fsync(output.fileno())
            # Keep the previous complete notebook before any destructive change.
            if self.path.exists():
                backup = self.path.with_suffix('.previous.json')
                backup_temp = backup.with_suffix('.tmp')
                try:
                    with self.path.open('rb') as source, backup_temp.open('wb') as target:
                        shutil.copyfileobj(source, target)
                        target.flush()
                        os.fsync(target.fileno())
                    os.replace(backup_temp, backup)
                finally:
                    backup_temp.unlink(missing_ok=True)
            digest = hashlib.sha256(temporary.read_bytes()).digest()
            os.replace(temporary, self.path)
            self._disk_digest = digest
            self._finish_committed_restore()
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

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
        if isinstance(body, str) and len(body.strip()) > MAX_INLINE_CHARACTERS:
            raise ValueError('Long notes must be prepared in the document store before saving.')
        return self.validate_note({"id": uuid.uuid4().hex, "title": title, "body": body, "created": now,
            "next_due": due if due is not None else now + repeat * 60 if repeat else None,
            "repeat_minutes": repeat, "done": False, "pinned": False, "unannounced": True, "source": source, **(details or {})})

    def add(self, title, body, repeat=30, due=None, now=None, source="", details=None):
        now = time.time() if now is None else now
        if details and any(key in details for key in ('document_id', 'body_characters', 'body_sha256')):
            raise ValueError('Use the document importer to attach full text to a note.')
        document = None
        try:
            if isinstance(body, str) and len(body.strip()) > MAX_INLINE_CHARACTERS:
                if len(body.strip()) + self.character_count() > MAX_DOCUMENT_CHARACTERS:
                    raise ValueError('Your notebook supports up to 1,000,000,000 characters.')
                document = self.documents.prepare_text(body.strip(), title=title,
                                                       other_note_characters=self.inline_character_count())
                document = self._verified_document(document)
                body, details = document['body'], {**(details or {}), **document}
            note = self.new_note(title, body, repeat, due, now, source, details)
            def change():
                self.notes.append(note)
                return note
            return self.transaction(change)
        except Exception:
            if document:
                self._discard_unreferenced([document['document_id']])
            raise

    def add_documents(self, sections, source_label="", now=None):
        """Save a reviewed import in one write, without creating timed reminders.

        Document provenance is separate from the watched text-file source. Long
        sections become separate notes rather than losing text to validation.
        """
        if not isinstance(sections, list) or not sections:
            raise ValueError("Choose document text to import.")
        now = time.time() if now is None else now
        label = self._source_label(source_label)
        reviewed = []
        characters = 0
        parts_count = 0
        for section in sections:
            if not isinstance(section, dict) or not isinstance(section.get("body"), str):
                raise ValueError("The document contains an invalid section. Nothing was saved.")
            body = section["body"].strip()
            if not body:
                continue
            characters += len(body)
            if characters + self.character_count() > MAX_DOCUMENT_CHARACTERS:
                raise ValueError("Your notebook supports up to 1,000,000,000 characters. Review a smaller selection.")
            title = clean_text(section.get("title"), 100) or "Imported document"
            reviewed.append((title, body))
            parts_count += (len(body) + MAX_INLINE_CHARACTERS - 1) // MAX_INLINE_CHARACTERS
        if not reviewed:
            raise ValueError("The reviewed document has no text to save.")
        if parts_count > MAX_DOCUMENT_NOTES:
            # Large reviewed imports retain their full text in one indexed
            # document rather than exhausting the small-note count.
            text = '\n\n'.join(body for _, body in reviewed)
            title = reviewed[0][0]
            document = self.documents.prepare_text(text, title=title,
                                                   other_note_characters=self.inline_character_count())
            return [self.add_prepared_document(document, title, label, now=now)]
        prepared = []
        for title, body in reviewed:
            parts = [body[start:start + 10000] for start in range(0, len(body), 10000)]
            for index, part in enumerate(parts, 1):
                suffix = f" · part {index}" if len(parts) > 1 else ""
                prepared.append((title[:100 - len(suffix)] + suffix, part))
        if len(self.notes) + len(prepared) > MAX_NOTES:
            raise ValueError("Your notebook is full. Nothing from this import was saved.")
        def change():
            added = []
            for title, body in prepared:
                note = self.new_note(title, body, 0, None, now,
                                     details={"document_source": label, "unannounced": False})
                self.notes.append(note)
                added.append(note)
            return added
        return self.transaction(change)

    def edit(self, identifier, title, body, repeat, due=None, now=None, details=None):
        now = time.time() if now is None else now
        note = self.find(identifier)
        if not note:
            raise ValueError("This note no longer exists.")
        if note.get('document_id') and body != note['body']:
            raise ValueError('This document is displayed in pages. Its preview is read-only; import a revised document to replace the full text.')
        if note.get('document_id') and details and any(details.get(key, note[key]) != note[key]
                                                       for key in ('document_id', 'body_characters', 'body_sha256')):
            raise ValueError('Document text cannot be replaced through metadata edits.')
        if not note.get('document_id') and details and any(key in details for key in ('document_id', 'body_characters', 'body_sha256')):
            raise ValueError('Use the document importer to attach full text to a note.')
        document = None
        if isinstance(body, str) and len(body.strip()) > MAX_INLINE_CHARACTERS:
            if len(body.strip()) + self.character_count() - len(note['body']) > MAX_DOCUMENT_CHARACTERS:
                raise ValueError('Your notebook supports up to 1,000,000,000 characters.')
            document = self.documents.prepare_text(body.strip(), title=title,
                                                   other_note_characters=self.inline_character_count() - len(note['body']))
            document = self._verified_document(document)
            body, details = document['body'], {**(details or {}), **document}
        updated = {**note, 'title': title, 'body': body, 'repeat_minutes': repeat,
                   'next_due': due if due is not None else now + repeat * 60 if repeat else None,
                   **(details or {})}
        updated = self.validate_note(updated)
        if not updated:
            raise ValueError("Write a note before saving it.")
        updated['updated'] = now
        if ((updated['title'], updated['body']) != (note['title'], note['body'])
                or updated.get('body_sha256') != note.get('body_sha256')):
            updated['written'] = now
        def change():
            note.update(updated)
            return note
        try:
            return self.transaction(change)
        except Exception:
            if document:
                self._discard_unreferenced([document['document_id']])
            raise

    def modify(self, identifier, **changes):
        note = self.find(identifier)
        if not note:
            return None
        if not note.get('document_id') and any(key in changes for key in ('document_id', 'body_characters', 'body_sha256')):
            raise ValueError('Use the document importer to attach full text to a note.')
        if note.get('document_id') and any(changes.get(key, note.get(key)) != note.get(key)
                                          for key in ('body', 'document_id', 'body_characters', 'body_sha256')):
            raise ValueError('This document preview is read-only. Import a revised document to replace the full text.')
        if not note.get('document_id') and isinstance(changes.get('body'), str) and len(changes['body'].strip()) > MAX_INLINE_CHARACTERS:
            return self.edit(identifier, changes.pop('title', note['title']), changes.pop('body'),
                             changes.pop('repeat_minutes', note['repeat_minutes']),
                             due=changes.pop('next_due', note['next_due']), details=changes)
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

    def next_notification(self, now, only_ids=None):
        # Shared reminders have employee-specific receipts and schedules. The
        # ordinary notebook scheduler must never broadcast them to every PC.
        active = [n for n in self.notes if not n["done"]
                  and not (n.get('team_id') or n.get('team_workspace_id'))
                  and (only_ids is None or n['id'] in only_ids)]
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
        document_id = (self.find(identifier) or {}).get('document_id')
        def change():
            self.state["notes"] = [n for n in self.notes if n["id"] != identifier]
        self.transaction(change)
        self._discard_unreferenced([document_id])

    def export_json(self):
        if any(note.get('document_id') for note in self.notes):
            raise ValueError('This notebook includes full documents. Use a ZIP backup to include all of their text.')
        return portable_notebook_state(self.state)

    def import_json(self, payload):
        return self.import_backup(payload)

    def import_backup(self, payload, allow_documents=False):
        if not isinstance(payload, dict) or not isinstance(payload.get('notes'), list) or len(payload['notes']) > MAX_NOTES:
            raise ValueError('Choose a Jeffery notebook backup with up to 2,000 notes.')
        incoming = {}
        for data in payload['notes']:
            if isinstance(data, dict) and data.get('document_id') and not allow_documents:
                raise ValueError('A JSON file contains document previews, not their full text. Restore a complete ZIP backup instead.')
            note = self.validate_note(data)
            if not note:
                raise ValueError('The backup contains an invalid note. Nothing was imported.')
            if note.get('document_id'):
                self._verified_document(note)
            note['source'] = ''
            note['pinned'] = False
            note['pin_position'] = None
            if note['id'] not in incoming or note['updated'] > incoming[note['id']]['updated']:
                incoming[note['id']] = note
        existing = {n['id']: n for n in self.notes}
        if len(set(existing) | set(incoming)) > MAX_NOTES:
            raise ValueError('Import would exceed the 2,000-note notebook limit.')
        previous_documents = {note.get('document_id') for note in self.notes}
        def change():
            count = 0
            for identifier, note in incoming.items():
                current = existing.get(identifier)
                if current is None:
                    note['unannounced'] = not note['done'] and not note.get('document_id')
                    self.notes.append(note)
                    count += 1
                elif note['updated'] > current['updated']:
                    replacement = {**note, 'pinned': current['pinned'], 'pin_position': current['pin_position'], 'source': current['source']}
                    current.clear()
                    current.update(replacement)
                    count += 1
            return count
        # A reviewed backup import is the explicit recovery action. Ordinary
        # saves must never silently replace an unreadable notebook.
        was_blocked = self.blocked_write
        if self._corrupt_backed_up and incoming:
            self.blocked_write = False
        try:
            count = self.transaction(change)
        except Exception:
            self.blocked_write = was_blocked
            raise
        self._finish_committed_restore(recover=False)
        self._discard_unreferenced(previous_documents)
        return count

    def search_ids(self, query):
        query = clean_text(query, 1000)
        if not query:
            return {note['id'] for note in self.notes}
        folded = query.casefold()
        found = {note['id'] for note in self.notes if folded in
                 (note_search_text(note) + ' ' + note.get('document_source', '')).casefold()}
        fuzzy_words = search_words(query) if not contains_secret(query) else ()
        found.update(note['id'] for note in self.notes
                     if typo_score(fuzzy_words, note_search_label(note)) >= 0.8)
        document_ids = {note['document_id'] for note in self.notes if note.get('document_id')}
        if document_ids:
            try:
                matching = self.documents.matching_ids(query, document_ids=list(document_ids))
            except (OSError, ValueError, sqlite3.Error):
                self.warning = 'Full document text could not be searched. Previews are still available; restore a complete notebook backup.'
                matching = set()
            found.update(note['id'] for note in self.notes if note.get('document_id') in matching)
        return found

    def recall_notes(self, query='', limit=12):
        """Return bounded copies with indexed excerpts, leaving persisted text intact."""
        limit = max(0, min(MAX_NOTES, limit)) if type(limit) is int else 12
        if not limit:
            return []
        query = clean_text(query, 1000)
        document_ids = {note['document_id'] for note in self.notes if note.get('document_id')}
        excerpts = {}
        if query and document_ids:
            try:
                rows = self.documents.search(query, document_ids=list(document_ids), limit=max(limit * 3, 12))
            except (OSError, ValueError, sqlite3.Error):
                self.warning = 'Full document text could not be searched. Previews are still available; restore a complete notebook backup.'
                rows = []
            for row in rows:
                identifier = row['document_id']
                text = row.get('text', '')
                if isinstance(text, str) and text:
                    excerpts[identifier] = (excerpts.get(identifier, '') + ('\n\n' if identifier in excerpts else '') + text)[:MAX_INLINE_CHARACTERS]
        terms = [word.casefold() for word in query.split() if len(word) > 2]
        fuzzy_words = search_words(query) if not contains_secret(query) else ()
        def rank(note):
            text = (note_search_text(note) + ' ' + note.get('document_source', '')).casefold()
            return (note.get('document_id') in excerpts, sum(term in text for term in terms),
                    typo_score(fuzzy_words, note_search_label(note)), not note['done'], note['updated'])
        selected = sorted(self.notes, key=rank, reverse=True)[:limit]
        result = []
        for note in selected:
            recalled = copy.deepcopy(note)
            if note.get('document_id') in excerpts:
                recalled['body'] = excerpts[note['document_id']]
            result.append(recalled)
        return result

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
        self.team_notifications_active = False
        self.team_excluded_note_ids = lambda: set()
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
        existing = self.store.find(identifier) if identifier else None
        if self.team_notifications_active:
            details = {**(details or {}), 'team_shared': True}
        if existing and existing.get('team_id'):
            # Explicit editor saves can change a shared schedule. Automatic
            # receipt updates and personal snoozes never pass through here.
            details = {**(details or {}), 'team_due_at': due}
        note = self.store.edit(identifier, title, body, repeat, due, details=details) if identifier else self.store.add(title, body, repeat, due, details=details)
        self.changed.emit(note["id"])
        return note

    def add_documents(self, sections, source_label=""):
        notes = self.store.add_documents(sections, source_label)
        self.changed.emit("")
        return notes

    def add_prepared_document(self, prepared, title, source_label=''):
        note = self.store.add_prepared_document(prepared, title, source_label)
        self.changed.emit(note['id'])
        return note

    def modify(self, identifier, **changes):
        existing = self.store.find(identifier)
        if self.team_notifications_active:
            changes['team_shared'] = True
        if existing and existing.get('team_id') and 'next_due' in changes and 'team_due_at' not in changes:
            changes['team_due_at'] = changes['next_due']
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
        # A folder workspace owns ordinary notes, including notes awaiting
        # their first sync. API-cached business records retain their existing
        # local scheduler rather than disappearing when folder sync connects.
        only_ids = self.team_excluded_note_ids() if self.team_notifications_active else None
        note, kind = self.store.next_notification(time.time(), only_ids=only_ids)
        if note:
            try:
                self.store.acknowledge(note["id"], kind, time.time())
                self.last_delivery = time.monotonic()
                self.notification.emit(dict(note), kind)
                self.changed.emit(note["id"])
            except (OSError, ValueError) as exc:
                self.error.emit(f"Could not save a reminder: {exc}")
