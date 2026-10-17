"""Verified local SQLite snapshots and stopped-server recovery.

Snapshots are not exports: they contain office accounts and private messages and
must stay in a directory accessible only to the server account/IT administrators.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
from threading import Event, Lock, Thread
import time

from .store import utc_timestamp


BACKUP_FORMAT = "jeffery-office-sqlite-v1"
BACKUP_NAME = re.compile(r"jeffery-office-\d{8}T\d{6}Z-[0-9a-f]{16}\.sqlite\Z")
DAY_SECONDS = 24 * 60 * 60
# SQLite's online backup can repeatedly restart if a busy database changes. Keep
# a busy database or a slow disk from leaving shutdown waiting indefinitely.
BACKUP_TIMEOUT_SECONDS = 120
REQUIRED_SCHEMA = {
    "users": {"id", "username", "password_hash", "created_at", "deleted_at"},
    "sessions": {"token_digest", "user_id", "expires_at"},
    "recovery_codes": {"user_id", "code_digest"},
    "messages": {"id", "sender_id", "recipient_id", "body", "created_at"},
    "business_state": {"id", "revision"},
    "business_entries": {"id", "revision", "deleted", "entry_json", "updated_by", "updated_at"},
    "it_admin": {"id", "password_hash", "must_change_password"},
    "it_admin_sessions": {"token_digest", "expires_at"},
    "it_admin_audit": {"id", "created_at", "action", "target_username"},
    "inventory_state": {"id", "revision", "last_import_at", "last_import_source"},
    "inventory_items": {"key", "sku", "description", "quantity", "location", "reorder_point",
                        "reorder_quantity", "target_stock", "supplier", "revision", "updated_at", "source"},
}


class BackupError(ValueError):
    """A recovery or configuration error safe to show to a local administrator."""


class BackupCancelled(BackupError):
    pass


def _local_path(value, *, must_exist=False):
    """Reject symlinks (including parent components) rather than follow them."""
    path = Path(value).expanduser().absolute()
    if str(path).startswith(("\\\\", "//")):
        raise BackupError("Use a local disk for the office database and backups.")
    for item in (path, *path.parents):
        if item.is_symlink():
            raise BackupError("Database and backup paths must not use symbolic links.")
        # Junctions on Windows can redirect a supposedly local directory too.
        if hasattr(item, "is_junction") and item.is_junction():
            raise BackupError("Database and backup paths must not use directory junctions.")
    if must_exist and not path.is_file():
        raise BackupError("The selected database or backup file does not exist.")
    return path


def _private_directory(path):
    path = _local_path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise BackupError("The backup location must be a directory.")
    # On Windows access comes from the server directory's inherited NTFS ACL.
    # chmod restricts POSIX snapshots; it does not pretend to configure NTFS ACLs.
    if os.name != "nt":
        path.chmod(0o700)
    return path


def _exclusive_file(path):
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    return os.fdopen(descriptor, "wb")


def _sync_directory(path):
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_only(path):
    # URI quoting by pathlib prevents '?' or '#' in filenames becoming options.
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)
    connection.execute("PRAGMA trusted_schema=OFF")
    return connection


class DatabaseLease:
    """Cross-process lock shared by the server and offline restore command.

    The lock file is intentionally never unlinked; deleting a locked inode would
    allow a second process to lock a different file with the same name.
    """

    def __init__(self, database):
        self.database = _local_path(database)
        self.path = self.database.with_name(self.database.name + ".server.lock")
        self._file = None
        self._release_guard = Lock()
        self._release_blocker = None
        self._release_waiter = None

    @property
    def acquired(self):
        return self._file is not None

    def acquire(self):
        if self.acquired:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _local_path(self.path)
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.path, flags, 0o600)
        handle = os.fdopen(descriptor, "r+b", buffering=0)
        try:
            if os.fstat(descriptor).st_size == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            handle.close()
            raise BackupError("The office database is in use. Stop the office server before restoring or starting a second server.") from None
        self._file = handle
        return self

    def defer_release_until(self, worker):
        """Keep the OS lock during a slow backup even after server shutdown.

        Cancellation normally finishes immediately. A filesystem call can still
        stall outside SQLite's progress hook, so a bounded shutdown must not
        release the lock while that worker might touch the database again.
        """
        with self._release_guard:
            self._release_blocker = worker

    def _release_after_worker(self, worker):
        worker.join()
        self.release()

    def release(self):
        with self._release_guard:
            if self._file is None:
                return
            worker = self._release_blocker
            if worker is not None and worker.is_alive():
                if self._release_waiter is None:
                    self._release_waiter = Thread(target=self._release_after_worker, args=(worker,),
                                                  name="office-backup-lease", daemon=True)
                    self._release_waiter.start()
                return
            handle, self._file = self._file, None
            self._release_blocker = self._release_waiter = None
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *unused):
        self.release()


def _check_stop(stop_event, deadline):
    if (stop_event is not None and stop_event.is_set()) or time.monotonic() >= deadline:
        raise BackupCancelled("The backup stopped before completion; no incomplete snapshot was published.")


def _hash_file(path, *, stop_event=None, deadline=None):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            if deadline is not None:
                _check_stop(stop_event, deadline)
            digest.update(chunk)
    return digest.hexdigest()


def _check_database(path, *, stop_event=None, deadline=None):
    with closing(_read_only(path)) as connection:
        if deadline is not None:
            connection.set_progress_handler(lambda: int((stop_event is not None and stop_event.is_set()) or time.monotonic() >= deadline), 1000)
        result = connection.execute("PRAGMA integrity_check").fetchall()
        if result != [("ok",)]:
            raise BackupError("The database failed its SQLite integrity check.")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise BackupError("The database contains broken account or message references.")
        for table, required in REQUIRED_SCHEMA.items():
            # Names are fixed constants, never supplied by a manifest.
            if connection.execute("SELECT type FROM sqlite_master WHERE name=?", (table,)).fetchone() != ("table",):
                raise BackupError("This is not a complete Jeffery office server backup.")
            fields = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            if not required <= fields:
                raise BackupError("This is not a complete Jeffery office server backup.")
        if connection.execute("SELECT COUNT(*) FROM it_admin WHERE id=1").fetchone()[0] != 1:
            raise BackupError("This backup has no IT administrator account.")


def _manifest(path):
    path = _local_path(path, must_exist=True)
    if not BACKUP_NAME.fullmatch(path.name):
        raise BackupError("Choose an original Jeffery office backup filename.")
    if any(path.with_name(path.name + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise BackupError("A snapshot must be standalone. Remove it from use as a live database before verifying.")
    manifest_path = _local_path(path.with_name(path.name + ".json"), must_exist=True)
    if manifest_path.stat().st_size > 4096:
        raise BackupError("The backup manifest is invalid.")
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise BackupError("The backup manifest could not be read.") from None
    if (not isinstance(value, dict) or value.get("format") != BACKUP_FORMAT
            or value.get("name") != path.name or type(value.get("size")) is not int
            or value["size"] <= 0 or value["size"] != path.stat().st_size
            or not isinstance(value.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"])
            or not isinstance(value.get("created_at"), str)
            or value.get("verified") is not True):
        raise BackupError("The backup manifest does not match the selected snapshot.")
    try:
        created = datetime.fromisoformat(value["created_at"].replace("Z", "+00:00"))
        if created.tzinfo is None or created.utcoffset().total_seconds() != 0:
            raise ValueError
        if not path.name.startswith("jeffery-office-" + created.strftime("%Y%m%dT%H%M%SZ") + "-"):
            raise ValueError
    except (ValueError, OverflowError):
        raise BackupError("The backup timestamp is invalid.") from None
    return path, value


def verify_backup(path, *, stop_event=None, deadline=None):
    """Verify the manifest, complete SHA-256, SQLite integrity and office schema."""
    path, value = _manifest(path)
    deadline = deadline if deadline is not None else time.monotonic() + BACKUP_TIMEOUT_SECONDS
    try:
        if _hash_file(path, stop_event=stop_event, deadline=deadline) != value["sha256"]:
            raise BackupError("The backup checksum does not match. Do not restore this file.")
        _check_database(path, stop_event=stop_event, deadline=deadline)
    except sqlite3.Error:
        raise BackupError("The backup database could not be verified.") from None
    return value


def create_backup(database, directory, *, clock=time.time, stop_event=None):
    """Publish a WAL-consistent snapshot only after its integrity and hash pass."""
    database = _local_path(database, must_exist=True)
    directory = _private_directory(directory)
    now = clock()
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"jeffery-office-{stamp}-{secrets.token_hex(8)}.sqlite"
    destination = directory / name
    temporary = directory / ("." + name + ".partial")
    manifest_path = destination.with_name(name + ".json")
    manifest_temporary = directory / ("." + name + ".json.partial")
    deadline = time.monotonic() + BACKUP_TIMEOUT_SECONDS
    published = False
    try:
        with _exclusive_file(temporary):
            pass
        with closing(_read_only(database)) as source, closing(sqlite3.connect(temporary)) as target:
            source.backup(target, pages=128, sleep=0.05,
                          progress=lambda *_: _check_stop(stop_event, deadline))
            # A standalone snapshot must never depend on a neighboring WAL.
            target.execute("PRAGMA journal_mode=DELETE")
        _check_stop(stop_event, deadline)
        _check_database(temporary, stop_event=stop_event, deadline=deadline)
        value = {"format": BACKUP_FORMAT, "name": name, "created_at": utc_timestamp(now),
                 "size": temporary.stat().st_size, "verified": True,
                 "sha256": _hash_file(temporary, stop_event=stop_event, deadline=deadline)}
        # Windows FlushFileBuffers needs a write-capable handle, even though
        # SQLite has already written and closed the snapshot itself.
        with temporary.open("r+b") as handle:
            os.fsync(handle.fileno())
        with _exclusive_file(manifest_temporary) as handle:
            handle.write((json.dumps(value, sort_keys=True) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        _check_stop(stop_event, deadline)
        os.replace(temporary, destination)
        os.replace(manifest_temporary, manifest_path)
        _sync_directory(directory)
        published = True
        return value
    except sqlite3.Error:
        raise BackupError("The office database could not be backed up. Check its integrity and available disk space.") from None
    finally:
        for path in (temporary, manifest_temporary):
            path.unlink(missing_ok=True)
        # A database without its matching manifest is never an accepted backup.
        if not published:
            destination.unlink(missing_ok=True)
            manifest_path.unlink(missing_ok=True)


def list_backups(directory):
    directory = _local_path(directory)
    if not directory.is_dir():
        return []
    values = []
    # Metadata only: creation and the explicit verify command check full contents.
    # Rehashing large backups every 2-second UI poll would stall the server disk.
    for path in directory.glob("jeffery-office-*.sqlite"):
        try:
            _, value = _manifest(path)
        except (BackupError, OSError):
            continue
        values.append(value)
    return sorted(values, key=lambda value: (value["created_at"], value["name"]), reverse=True)


def prune_backups(directory, keep_days, *, stop_event=None):
    """Keep the newest snapshot for each of the latest N backup dates.

    Manual snapshots replace that day's older snapshot only after successful
    publication. Unrecognized files and damaged snapshots are never deleted.
    """
    days = set()
    deadline = time.monotonic() + BACKUP_TIMEOUT_SECONDS
    for value in list_backups(directory):
        _check_stop(stop_event, deadline)
        day = value["created_at"][:10]
        path = Path(directory) / value["name"]
        try:
            # A damaged newer file must not displace a usable older snapshot,
            # including an older snapshot from the same date.
            verify_backup(path, stop_event=stop_event, deadline=deadline)
        except BackupCancelled:
            raise
        except (BackupError, OSError):
            continue
        if day not in days and len(days) < keep_days:
            days.add(day)
            continue
        path.unlink()
        path.with_name(path.name + ".json").unlink()


class BackupManager:
    """One bounded background worker for automatic and administrator snapshots."""

    def __init__(self, store, admin, *, directory=None, enabled=False, keep_days=14):
        if type(keep_days) is not int or not 1 <= keep_days <= 365:
            raise BackupError("Keep between 1 and 365 days of office backups.")
        self.store, self.admin = store, admin
        self.directory = _local_path(directory if directory is not None else store.path.parent / "office-backups")
        self.enabled, self.keep_days = bool(enabled), keep_days
        self._lock = Lock()
        self._stop = Event()
        self._wake = Event()
        self._thread = None
        self._running = False
        self._manual = False
        self._last_error = None
        self._last_success = None
        self._retry_at = 0.0

    def _load_latest(self):
        latest = list_backups(self.directory)
        if latest:
            self._last_success = latest[0]["created_at"]
        return latest

    def _next_due_epoch(self):
        if self._last_success is None:
            return self.store.clock()
        timestamp = datetime.fromisoformat(self._last_success.replace("Z", "+00:00")).timestamp()
        # One automatic snapshot per UTC date, also catches up after downtime.
        return (int(timestamp) // DAY_SECONDS + 1) * DAY_SECONDS

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if self._stop.is_set():
                raise BackupError("The backup service is shutting down.")
            self._load_latest()
            self._thread = Thread(target=self._worker, name="office-backups", daemon=True)
            self._thread.start()

    def stop(self, timeout=3):
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        stopped = thread is None or not thread.is_alive()
        if stopped:
            with self._lock:
                self._running = self._manual = False
        return stopped

    def status(self):
        with self._lock:
            values = self._load_latest()
            due = max(self._next_due_epoch(), self._retry_at) if self.enabled else None
            return {"enabled": self.enabled, "running": self._running,
                    "last_success_at": self._last_success, "last_error": self._last_error,
                    "next_due": utc_timestamp(due) if due is not None else None,
                    "directory": str(self.directory), "keep_days": self.keep_days,
                    "backups": [{key: value[key] for key in ("name", "created_at", "size", "verified")}
                                for value in values[:365]]}

    def request(self, authorize):
        """Reserve work and perform the session recheck/audit before it can run."""
        with self._lock:
            if self._stop.is_set():
                raise BackupError("The backup service is shutting down.")
            if self._running or self._manual:
                return False
            authorize()
            self._manual = True
            self._running = True
        try:
            self.start()
        except Exception:
            with self._lock:
                self._manual = self._running = False
            raise
        self._wake.set()
        return True

    def _audit(self, action):
        with closing(self.store._connect()) as connection, connection:
            self.admin._audit(connection, action)

    def _worker(self):
        while not self._stop.is_set():
            with self._lock:
                manual = self._manual
                self._manual = False
                due = self.enabled and self.store.clock() >= max(self._next_due_epoch(), self._retry_at)
                run = manual or due
                if run:
                    self._running = True
            if not run:
                self._wake.wait(timeout=30)
                self._wake.clear()
                continue
            try:
                value = create_backup(self.store.path, self.directory, clock=self.store.clock, stop_event=self._stop)
                prune_backups(self.directory, self.keep_days, stop_event=self._stop)
                self._audit("backup_completed")
                with self._lock:
                    self._last_success = value["created_at"]
                    self._last_error = None
                    self._retry_at = 0.0
            except Exception:
                # Raw exception messages may contain paths, SQL, or private data.
                with self._lock:
                    self._last_error = ("Backup stopped during server shutdown. The previous snapshots are unchanged."
                                        if self._stop.is_set() else
                                        "Backup failed. Check server disk space and permissions, then try again.")
                    self._retry_at = self.store.clock() + 3600
                try:
                    self._audit("backup_failed")
                except Exception:
                    pass
            finally:
                with self._lock:
                    self._running = False


def restore_backup(backup, database, *, confirm=False):
    """Offline recovery with a safety snapshot, explicit confirmation and lock.

    The destination must already exist to prevent a mistyped path from silently
    restoring into a different office database. All restored sessions are revoked.
    """
    if not confirm:
        raise BackupError("Restore replaces office data. Stop the server and pass --confirm-restore after verifying the backup and database paths.")
    backup = _local_path(backup, must_exist=True)
    database = _local_path(database, must_exist=True)
    if backup.samefile(database):
        raise BackupError("The backup and destination must be different files.")
    with DatabaseLease(database):
        manifest = verify_backup(backup)
        safety_directory = database.parent / "restore-safety-backups"
        safety = create_backup(database, safety_directory)
        staged = database.with_name("." + database.name + ".restore-" + secrets.token_hex(8))
        try:
            with _exclusive_file(staged) as target, backup.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            # Verify the actual staged bytes, not just a source that could change
            # between verification and copying.
            if _hash_file(staged) != manifest["sha256"]:
                raise BackupError("The backup changed during recovery. The office database was not replaced.")
            with closing(sqlite3.connect(staged, timeout=1)) as connection, connection:
                connection.execute("PRAGMA trusted_schema=OFF")
                connection.execute("PRAGMA journal_mode=DELETE")
                connection.execute("DELETE FROM sessions")
                connection.execute("DELETE FROM it_admin_sessions")
                connection.execute("INSERT INTO it_admin_audit(created_at,action,target_username) VALUES(?,?,?)",
                                   (utc_timestamp(time.time()), "backup_restored", "admin"))
            _check_database(staged)
            with staged.open("r+b") as handle:
                os.fsync(handle.fileno())
            # Convert/checkpoint the old database before replacement so no old
            # WAL can replay over the restored data. Active legacy readers/writers
            # that do not use DatabaseLease also cause this operation to fail.
            with closing(sqlite3.connect(database, timeout=1)) as current:
                row = current.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if row and row[0]:
                    raise BackupError("The office database is busy. Stop every server process before restoring.")
                if current.execute("PRAGMA journal_mode=DELETE").fetchone()[0].lower() != "delete":
                    raise BackupError("The office database is busy. Stop every server process before restoring.")
                current.execute("BEGIN EXCLUSIVE")
                current.rollback()
            os.replace(staged, database)
            _sync_directory(database.parent)
        except sqlite3.Error:
            raise BackupError("Restore could not finish. Stop every server process and check the database. The safety snapshot is retained.") from None
        finally:
            staged.unlink(missing_ok=True)
        return {"restored": manifest["name"], "database": str(database),
                "safety_backup": str(safety_directory / safety["name"]), "sessions_revoked": True}
