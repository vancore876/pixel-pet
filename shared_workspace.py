"""Durable, bounded file transport for an explicitly selected team share.

Only immutable JSON events and UTF-8 document blobs cross the share. Each PC
keeps its own database. Windows publishes with a non-replacing rename; POSIX
uses a non-replacing hard link so two writers never overwrite one another.
"""
from __future__ import annotations

import codecs
from collections.abc import Mapping
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time
import uuid


MAX_EVENT_BYTES = 256 * 1024
MAX_DOCUMENT_CHARACTERS = 1_000_000_000
MAX_DOCUMENT_BYTES = 4 * MAX_DOCUMENT_CHARACTERS
COPY_BYTES = 512 * 1024
_EVENT_ID = re.compile(r"[0-9a-f]{32}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")


class SharedWorkspaceError(Exception):
    """The share is unavailable, unsafe, corrupt, or contains a conflict."""


class SharedWorkspaceCancelled(SharedWorkspaceError):
    """A streamed operation was cancelled before its destination changed."""


def _check_cancel(cancel):
    if cancel is not None:
        cancelled = cancel() if callable(cancel) else cancel.is_set()
        if cancelled:
            raise SharedWorkspaceCancelled("Shared document transfer cancelled.")


def _json_bytes(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise SharedWorkspaceError("Shared data must be valid UTF-8 JSON.") from exc


def _json_object(raw):
    def reject_constant(value):
        raise ValueError("Non-finite JSON number")

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), parse_constant=reject_constant,
                           object_pairs_hook=unique_pairs)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise SharedWorkspaceError("Invalid shared JSON file.") from exc
    if not isinstance(value, dict):
        raise SharedWorkspaceError("Shared JSON must contain an object.")
    return value


def _linked(path):
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _regular(path):
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise SharedWorkspaceError("Shared file is unavailable.") from exc
    if not stat.S_ISREG(mode) or _linked(path):
        raise SharedWorkspaceError("Shared file must be a regular file.")


def _cleanup(path):
    # A disconnected share may retain a staged file. Readers ignore these files;
    # cleanup must never hide the transfer error or change a final destination.
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _read_small(path, maximum=MAX_EVENT_BYTES):
    _regular(path)
    try:
        with path.open("rb") as source:
            raw = source.read(maximum + 1)
    except OSError as exc:
        raise SharedWorkspaceError("Could not read the shared file.") from exc
    if len(raw) > maximum:
        raise SharedWorkspaceError("Shared file exceeds its size limit.")
    return raw


def _stage_bytes(directory, raw):
    path = directory / ("." + uuid.uuid4().hex + ".tmp")
    try:
        with path.open("xb") as target:
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
    except OSError as exc:
        _cleanup(path)
        raise SharedWorkspaceError("Could not write to the shared folder.") from exc
    return path


def _publish_new(staged, destination):
    """Atomically create a complete file, returning False if it already exists."""
    try:
        if os.name == "nt":
            os.rename(staged, destination)  # Windows rename never replaces.
        else:
            os.link(staged, destination)
        return True
    except FileExistsError:
        return False
    except OSError as exc:
        raise SharedWorkspaceError("Could not publish to the shared folder.") from exc
    finally:
        _cleanup(staged)


def _replace_bytes(destination, raw):
    staged = _stage_bytes(destination.parent, raw)
    try:
        os.replace(staged, destination)
    except OSError as exc:
        raise SharedWorkspaceError("Could not update the shared file.") from exc
    finally:
        _cleanup(staged)


def _character_count(value):
    if (isinstance(value, bool) or not isinstance(value, int)
            or not 0 <= value <= MAX_DOCUMENT_CHARACTERS):
        raise SharedWorkspaceError("Document character count is invalid.")
    return value


def _copy_utf8(source, target=None, *, cancel=None):
    """Read bounded blocks, validating UTF-8 and counting code points exactly."""
    digest = hashlib.sha256()
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    characters = byte_count = 0
    header = bytearray()
    _check_cancel(cancel)
    _regular(source)
    try:
        if source.stat().st_size > MAX_DOCUMENT_BYTES:
            raise SharedWorkspaceError("Shared document exceeds the byte limit.")
        with source.open("rb") as incoming:
            while True:
                _check_cancel(cancel)
                block = incoming.read(COPY_BYTES)
                if not block:
                    break
                if len(header) < 16:
                    header.extend(block[:16 - len(header)])
                if bytes(header) == b"SQLite format 3\0":
                    raise SharedWorkspaceError("Share document text, never a SQLite database.")
                byte_count += len(block)
                characters += len(decoder.decode(block, final=False))
                if (byte_count > MAX_DOCUMENT_BYTES
                        or characters > MAX_DOCUMENT_CHARACTERS):
                    raise SharedWorkspaceError("Shared document exceeds the notebook limit.")
                digest.update(block)
                if target is not None:
                    target.write(block)
            characters += len(decoder.decode(b"", final=True))
        _check_cancel(cancel)
    except UnicodeError as exc:
        raise SharedWorkspaceError("Shared documents must contain valid UTF-8 text.") from exc
    except OSError as exc:
        raise SharedWorkspaceError("Shared document transfer failed.") from exc
    return {"sha256": digest.hexdigest(), "characters": characters, "bytes": byte_count}


class SharedWorkspace:
    """Transport attached to an existing user-selected directory or UNC share."""

    def __init__(self, selected_folder):
        self.selected_folder = Path(selected_folder).expanduser().absolute()
        try:
            if not self.selected_folder.is_dir():
                raise SharedWorkspaceError("Choose an existing shared folder.")
            # Resolve the explicitly selected root, then disallow redirected children.
            self.selected_folder = self.selected_folder.resolve(strict=True)
            self.path = self.selected_folder / "JefferyWorkspace"
            self._ensure_directory(self.path)
            self.events_path = self.path / "events"
            self.blobs_path = self.path / "blobs"
            self.notifications_path = self.path / "notifications"
            for child in (self.events_path, self.blobs_path, self.notifications_path):
                self._ensure_directory(child)
            self.manifest_path = self.path / "workspace.json"
            if not self.manifest_path.exists():
                raw = _json_bytes({"version": 1, "workspace_id": uuid.uuid4().hex})
                _publish_new(_stage_bytes(self.path, raw), self.manifest_path)
            self.workspace_id = self._read_manifest()
        except OSError as exc:
            raise SharedWorkspaceError("The selected shared folder is unavailable.") from exc

    @staticmethod
    def _ensure_directory(path):
        if _linked(path):
            raise SharedWorkspaceError("Workspace folders cannot redirect to another path.")
        path.mkdir(exist_ok=True)
        if not path.is_dir() or _linked(path):
            raise SharedWorkspaceError("Workspace path must be a regular directory.")

    def _read_manifest(self):
        value = _json_object(_read_small(self.manifest_path, 1024))
        identifier = value.get("workspace_id")
        if (value.get("version") != 1 or isinstance(value.get("version"), bool)
                or not isinstance(identifier, str) or not _EVENT_ID.fullmatch(identifier)):
            raise SharedWorkspaceError("Shared workspace identity is invalid.")
        return identifier

    def _available(self):
        try:
            for directory in (self.selected_folder, self.path, self.events_path,
                              self.blobs_path, self.notifications_path):
                if not directory.is_dir() or _linked(directory):
                    raise SharedWorkspaceError("Shared workspace is unavailable.")
            if self._read_manifest() != self.workspace_id:
                raise SharedWorkspaceError("The selected folder contains a different workspace.")
        except OSError as exc:
            raise SharedWorkspaceError("Shared workspace is unavailable.") from exc

    def _validate_event(self, event, identifier=None):
        if not isinstance(event, Mapping):
            raise SharedWorkspaceError("Shared event must be a JSON object.")
        value = dict(event)
        event_id = value.get("event_id")
        if not isinstance(event_id, str) or not _EVENT_ID.fullmatch(event_id):
            raise SharedWorkspaceError("Shared event ID is invalid.")
        if identifier is not None and event_id != identifier:
            raise SharedWorkspaceError("Shared event filename does not match its ID.")
        if value.get("workspace_id", self.workspace_id) != self.workspace_id:
            raise SharedWorkspaceError("Shared event belongs to another workspace.")
        value["workspace_id"] = self.workspace_id
        return value

    def publish_event(self, event):
        """Publish once; replay is safe, but reusing an ID for different data fails."""
        self._available()
        value = self._validate_event(event)
        raw = _json_bytes(value)
        if len(raw) > MAX_EVENT_BYTES:
            raise SharedWorkspaceError("Shared event exceeds the 256 KiB limit.")
        destination = self.events_path / (value["event_id"] + ".json")
        created = _publish_new(_stage_bytes(self.events_path, raw), destination)
        if not created:
            existing = _read_small(destination)
            if (len(existing) != len(raw)
                    or hashlib.sha256(existing).digest() != hashlib.sha256(raw).digest()
                    or existing != raw):
                raise SharedWorkspaceError("A different shared event already uses this ID.")
        return created

    def read_events(self):
        """Return valid events in deterministic order and bounded diagnostic warnings."""
        self._available()
        events, warnings = [], []
        warning_count = 0
        try:
            entries = sorted(self.events_path.iterdir(), key=lambda item: item.name)
            for path in entries:
                if path.name.startswith(".") and path.name.endswith(".tmp"):
                    continue
                identifier = path.name[:-5] if path.name.endswith(".json") else ""
                try:
                    if not _EVENT_ID.fullmatch(identifier):
                        raise SharedWorkspaceError("Invalid event filename.")
                    value = _json_object(_read_small(path))
                    if value.get("workspace_id") != self.workspace_id:
                        raise SharedWorkspaceError("Event workspace does not match.")
                    events.append(self._validate_event(value, identifier))
                except SharedWorkspaceError as exc:
                    warning_count += 1
                    if len(warnings) < 100:
                        warnings.append(f"Ignored shared event {path.name[:80]}: {exc}")
        except OSError as exc:
            raise SharedWorkspaceError("Could not read shared events.") from exc
        if warning_count > len(warnings):
            warnings.append(f"Ignored {warning_count - len(warnings)} additional invalid events.")
        return events, warnings

    def upload_blob(self, source, expected_characters=None, cancel=None):
        """Stream text into a content-addressed immutable blob; never copy a database."""
        if expected_characters is not None:
            _character_count(expected_characters)
        self._available()
        _check_cancel(cancel)
        staged = self.blobs_path / ("." + uuid.uuid4().hex + ".tmp")
        try:
            with staged.open("xb") as target:
                result = _copy_utf8(Path(source), target, cancel=cancel)
                if (expected_characters is not None
                        and result["characters"] != expected_characters):
                    raise SharedWorkspaceError("Document character count does not match.")
                target.flush()
                os.fsync(target.fileno())
            _check_cancel(cancel)
            destination = self.blobs_path / (result["sha256"] + ".txt")
            if not _publish_new(staged, destination):
                if _copy_utf8(destination, cancel=cancel) != result:
                    raise SharedWorkspaceError("The existing shared document is corrupt.")
            return result
        except OSError as exc:
            raise SharedWorkspaceError("Could not upload the shared document.") from exc
        finally:
            _cleanup(staged)

    def download_blob(self, sha256, destination, expected_characters, cancel=None):
        """Verify hash and UTF-8 character count before atomically replacing local text."""
        if not isinstance(sha256, str) or not _HASH.fullmatch(sha256):
            raise SharedWorkspaceError("Shared document hash is invalid.")
        _character_count(expected_characters)
        self._available()
        _check_cancel(cancel)
        destination = Path(destination)
        if _linked(destination):
            raise SharedWorkspaceError("Document destination cannot be a redirected path.")
        if destination.resolve().is_relative_to(self.path):
            raise SharedWorkspaceError("Download documents to local storage outside the workspace.")
        staged = destination.parent / ("." + uuid.uuid4().hex + ".tmp")
        try:
            with staged.open("xb") as target:
                result = _copy_utf8(self.blobs_path / (sha256 + ".txt"), target, cancel=cancel)
                if (result["sha256"] != sha256
                        or result["characters"] != expected_characters):
                    raise SharedWorkspaceError("Shared document failed its integrity check.")
                target.flush()
                os.fsync(target.fileno())
            _check_cancel(cancel)
            os.replace(staged, destination)
        except OSError as exc:
            raise SharedWorkspaceError("Could not save the shared document locally.") from exc
        finally:
            _cleanup(staged)

    @staticmethod
    def _notification_key(employee_id, entity_id, occurrence_id):
        identifiers = (employee_id, entity_id, occurrence_id)
        if any(not isinstance(value, str) or not value or len(value) > 1024
               for value in identifiers):
            raise SharedWorkspaceError("Notification identity is invalid.")
        return hashlib.sha256(_json_bytes(list(identifiers))).hexdigest()

    @staticmethod
    def _now(now):
        value = time.time() if now is None else now
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise SharedWorkspaceError("Notification time is invalid.")
        return float(value)

    @contextmanager
    def _notification_lock(self, key):
        """A crashed PC releases the lock; the lease itself remains durable."""
        self._available()
        path = self.notifications_path / (key + ".lock")
        if _linked(path):
            raise SharedWorkspaceError("Notification lock path is invalid.")
        descriptor = None
        locked = False
        try:
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise SharedWorkspaceError("Notification lock must be a regular file.")
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
            yield
        except OSError as exc:
            raise SharedWorkspaceError("Shared notification claim is unavailable or busy.") from exc
        finally:
            if descriptor is not None:
                if locked:
                    if os.name == "nt":
                        import msvcrt
                        os.lseek(descriptor, 0, os.SEEK_SET)
                        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    def _claim_record(self, key):
        path = self.notifications_path / (key + ".claim.json")
        if _linked(path):
            raise SharedWorkspaceError("Shared notification claim path is invalid.")
        if not path.exists():
            return None
        record = _json_object(_read_small(path, 4096))
        token, expiry = record.get("token"), record.get("expires_at")
        if (not isinstance(token, str) or not _EVENT_ID.fullmatch(token)
                or isinstance(expiry, bool) or not isinstance(expiry, (int, float))
                or not math.isfinite(expiry) or record.get("key") != key):
            raise SharedWorkspaceError("Shared notification claim is invalid.")
        return record

    def _presented(self, key):
        path = self.notifications_path / (key + ".shown.json")
        if _linked(path):
            raise SharedWorkspaceError("Shared notification marker path is invalid.")
        if not path.exists():
            return False
        record = _json_object(_read_small(path, 4096))
        if record.get("key") != key or record.get("presented") is not True:
            raise SharedWorkspaceError("Shared notification marker is invalid.")
        return True

    def claim_notification(self, employee_id, entity_id, occurrence_id, *, lease_seconds=30, now=None):
        """Reserve one employee's occurrence across PCs; no claim is made offline."""
        key = self._notification_key(employee_id, entity_id, occurrence_id)
        current = self._now(now)
        if (isinstance(lease_seconds, bool) or not isinstance(lease_seconds, (int, float))
                or not math.isfinite(lease_seconds) or not 1 <= lease_seconds <= 300):
            raise SharedWorkspaceError("Notification lease must be between 1 and 300 seconds.")
        with self._notification_lock(key):
            if self._presented(key):
                return None
            existing = self._claim_record(key)
            if existing and existing["expires_at"] > current:
                return None
            token = uuid.uuid4().hex
            _replace_bytes(self.notifications_path / (key + ".claim.json"),
                           _json_bytes({"key": key, "token": token,
                                        "expires_at": current + lease_seconds}))
            return token

    def complete_notification(self, employee_id, entity_id, occurrence_id, token, *, now=None):
        """Permanently mark a successfully shown popup, without acknowledging content."""
        key = self._notification_key(employee_id, entity_id, occurrence_id)
        current = self._now(now)
        if not isinstance(token, str) or not _EVENT_ID.fullmatch(token):
            raise SharedWorkspaceError("Notification claim token is invalid.")
        with self._notification_lock(key):
            if self._presented(key):
                return True
            existing = self._claim_record(key)
            if not existing or existing["token"] != token:
                return False
            path = self.notifications_path / (key + ".shown.json")
            raw = _json_bytes({"key": key, "presented": True, "presented_at": current})
            _publish_new(_stage_bytes(self.notifications_path, raw), path)
            (self.notifications_path / (key + ".claim.json")).unlink(missing_ok=True)
            return True

    def release_notification(self, employee_id, entity_id, occurrence_id, token, *, now=None):
        """Release this caller's failed presentation so another PC can retry."""
        key = self._notification_key(employee_id, entity_id, occurrence_id)
        self._now(now)
        if not isinstance(token, str) or not _EVENT_ID.fullmatch(token):
            raise SharedWorkspaceError("Notification claim token is invalid.")
        with self._notification_lock(key):
            existing = self._claim_record(key)
            if not existing or existing["token"] != token:
                return False
            (self.notifications_path / (key + ".claim.json")).unlink()
            return True
