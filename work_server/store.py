"""Server-local SQLite storage, password hashing, and revocable chat sessions."""
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
from threading import BoundedSemaphore
import time

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from server_business_schema import BusinessValidationError, validate_business_entry, validate_business_id


USERNAME_PATTERN = re.compile(r"[A-Za-z0-9_.-]{3,32}\Z")


class StoreError(Exception):
    """An expected, safe-to-display API error."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def validate_username(username: str) -> str:
    if not isinstance(username, str) or not USERNAME_PATTERN.fullmatch(username):
        raise StoreError(422, "Username must be 3–32 ASCII letters, numbers, dots, underscores, or hyphens.")
    return username


def validate_password(password: str) -> str:
    # Spaces can be part of a passphrase. Never trim or otherwise alter a password.
    if not isinstance(password, str) or not 12 <= len(password) <= 128:
        raise StoreError(422, "Password must contain 12–128 characters.")
    return password


def utc_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ChatStore:
    def __init__(self, database_path, *, clock=time.time, session_seconds=8 * 60 * 60,
                 hash_slots=4, max_users=1000, max_business_entries=2000):
        self.path = Path(database_path).expanduser().resolve()
        if str(self.path).startswith("\\\\"):
            raise ValueError("Keep the chat database on the server's local disk, not a network share.")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        self.clock = clock
        self.session_seconds = session_seconds
        self.max_users = max_users
        self.max_business_entries = max_business_entries
        self.hasher = PasswordHasher()  # Argon2id, independently salted on every hash.
        self.hash_slots = BoundedSemaphore(hash_slots)
        # Unknown usernames still perform the same password-verification work.
        self.dummy_hash = self.hasher.hash(secrets.token_urlsafe(32))
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    deleted_at TEXT
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_digest TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
                CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
                CREATE TABLE IF NOT EXISTS recovery_codes (
                    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    code_digest TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sender_id INTEGER NOT NULL REFERENCES users(id),
                    recipient_id INTEGER REFERENCES users(id),
                    body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 4000),
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS messages_recipient ON messages(recipient_id, id);
                CREATE INDEX IF NOT EXISTS messages_pair ON messages(sender_id, recipient_id, id);
                CREATE TABLE IF NOT EXISTS read_receipts (
                    reader_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    conversation_id INTEGER NOT NULL CHECK(conversation_id >= 0),
                    through_id INTEGER NOT NULL REFERENCES messages(id),
                    PRIMARY KEY(reader_id, conversation_id)
                );
                CREATE INDEX IF NOT EXISTS read_receipts_conversation ON read_receipts(conversation_id, through_id);
                CREATE TABLE IF NOT EXISTS business_state (
                    id INTEGER PRIMARY KEY CHECK(id = 1),
                    revision INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO business_state(id, revision) VALUES (1, 0);
                CREATE TABLE IF NOT EXISTS business_entries (
                    id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL UNIQUE,
                    deleted INTEGER NOT NULL DEFAULT 0,
                    entry_json TEXT,
                    updated_by INTEGER NOT NULL REFERENCES users(id),
                    updated_at TEXT NOT NULL
                );
            """)
            # Existing office databases predate account deletion. Serialize the
            # additive migration so two startup processes cannot add it twice.
            connection.execute("BEGIN IMMEDIATE")
            if "deleted_at" not in {row["name"] for row in connection.execute("PRAGMA table_info(users)")}:
                connection.execute("ALTER TABLE users ADD COLUMN deleted_at TEXT")

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _hash(self, password):
        if not self.hash_slots.acquire(blocking=False):
            raise StoreError(429, "Sign-in is busy. Wait a moment and try again.")
        try:
            return self.hasher.hash(password)
        finally:
            self.hash_slots.release()

    def _verify(self, password_hash, password):
        if not self.hash_slots.acquire(blocking=False):
            raise StoreError(429, "Sign-in is busy. Wait a moment and try again.")
        try:
            try:
                return self.hasher.verify(password_hash, password)
            except (VerifyMismatchError, VerificationError, InvalidHashError):
                return False
        finally:
            self.hash_slots.release()

    def _issue_session(self, connection, user_id, username):
        token = secrets.token_urlsafe(32)
        expires_at = self.clock() + self.session_seconds
        connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (self.clock(),))
        connection.execute("INSERT INTO sessions(token_digest, user_id, expires_at) VALUES (?, ?, ?)",
                           (hashlib.sha256(token.encode("ascii")).hexdigest(), user_id, expires_at))
        # Capture the first-login feed baseline in this auth transaction. If the
        # client waited for its first poll to baseline, messages arriving after
        # successful sign-in but before that poll would never produce an alert.
        notification_cursor = connection.execute("""
            SELECT COALESCE(MAX(id), 0) FROM messages
            WHERE sender_id != ? AND (recipient_id IS NULL OR recipient_id = ?)
        """, (user_id, user_id)).fetchone()[0]
        return {"token": token, "user": {"id": user_id, "username": username},
                "expires_at": utc_timestamp(expires_at), "notification_cursor": notification_cursor}

    @staticmethod
    def _issue_recovery_code(connection, user_id):
        # A recovery code is a one-use credential: retain only its SHA-256 digest.
        # 32 random bytes make offline guessing impractical without password KDF work.
        code = secrets.token_urlsafe(32)
        connection.execute("""
            INSERT INTO recovery_codes(user_id, code_digest) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET code_digest = excluded.code_digest
        """, (user_id, hashlib.sha256(code.encode("ascii")).hexdigest()))
        return code

    def register(self, username, password):
        validate_username(username)
        validate_password(password)
        with closing(self._connect()) as connection:
            if connection.execute("SELECT COUNT(*) FROM users WHERE deleted_at IS NULL").fetchone()[0] >= self.max_users:
                raise StoreError(503, "This server has reached its account capacity. Contact the server administrator.")
        password_hash = self._hash(password)
        with closing(self._connect()) as connection, connection:
            # Serialize the capacity check and insertion across simultaneous signups.
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT COUNT(*) FROM users WHERE deleted_at IS NULL").fetchone()[0] >= self.max_users:
                raise StoreError(503, "This server has reached its account capacity. Contact the server administrator.")
            try:
                cursor = connection.execute("INSERT INTO users(username, password_hash, created_at) VALUES (?, ?, ?)",
                                            (username, password_hash, utc_timestamp(self.clock())))
            except sqlite3.IntegrityError:
                raise StoreError(409, "That username is already registered.") from None
            session = self._issue_session(connection, cursor.lastrowid, username)
            session["recovery_code"] = self._issue_recovery_code(connection, cursor.lastrowid)
            return session

    def login(self, username, password):
        validate_username(username)
        validate_password(password)
        with closing(self._connect()) as connection:
            user = connection.execute("SELECT id, username, password_hash FROM users WHERE username = ? AND deleted_at IS NULL",
                                      (username,)).fetchone()
        password_hash = user["password_hash"] if user else self.dummy_hash
        if not self._verify(password_hash, password) or user is None:
            raise StoreError(401, "Username or password is incorrect.")
        # A concurrent administrator reset must not allow a previously read password to sign in.
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute("SELECT password_hash FROM users WHERE id = ? AND deleted_at IS NULL", (user["id"],)).fetchone()
            if current is None or current["password_hash"] != password_hash:
                raise StoreError(401, "Username or password is incorrect.")
            return self._issue_session(connection, user["id"], user["username"])

    def authenticate(self, token):
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise StoreError(401, "Sign in to use work chat.")
        token_digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        with closing(self._connect()) as connection:
            user = connection.execute("""
                SELECT users.id, users.username FROM sessions JOIN users ON users.id = sessions.user_id
                WHERE sessions.token_digest = ? AND sessions.expires_at > ? AND users.deleted_at IS NULL
            """, (token_digest, self.clock())).fetchone()
        if user is None:
            raise StoreError(401, "Your session has ended. Sign in again.")
        return {"id": user["id"], "username": user["username"]}

    def logout(self, token):
        with closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM sessions WHERE token_digest = ?",
                               (hashlib.sha256(token.encode("ascii")).hexdigest(),))

    def users(self):
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT id, username FROM users WHERE deleted_at IS NULL ORDER BY username COLLATE NOCASE LIMIT ?",
                                      (self.max_users + 1,)).fetchall()
            if len(rows) > self.max_users:
                raise StoreError(503, "The user directory exceeds this server's account capacity. Contact the administrator.")
            return [dict(row) for row in rows]

    @staticmethod
    def require_active_user(connection, actor_id):
        """Recheck identity under each write lock, including in-flight requests."""
        if connection.execute("SELECT id FROM users WHERE id = ? AND deleted_at IS NULL", (actor_id,)).fetchone() is None:
            raise StoreError(401, "Your session has ended. Sign in again.")

    @staticmethod
    def _ensure_peer(connection, peer_id, *, include_deleted=False):
        if type(peer_id) is not int or peer_id < 1:
            raise StoreError(422, "Choose a valid recipient.")
        condition = "" if include_deleted else " AND deleted_at IS NULL"
        if connection.execute("SELECT id FROM users WHERE id = ?" + condition, (peer_id,)).fetchone() is None:
            raise StoreError(404, "That user was not found.")

    def messages(self, actor_id, *, peer_id=None, after_id=None, limit=100):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise StoreError(422, "Message limit must be between 1 and 100.")
        if after_id is not None and (type(after_id) is not int or after_id < 0):
            raise StoreError(422, "Message cursor must be a nonnegative integer.")
        with closing(self._connect()) as connection:
            if peer_id is None:
                condition, values = "m.recipient_id IS NULL", []
            else:
                self._ensure_peer(connection, peer_id, include_deleted=True)
                condition = "((m.sender_id = ? AND m.recipient_id = ?) OR (m.sender_id = ? AND m.recipient_id = ?))"
                values = [actor_id, peer_id, peer_id, actor_id]
            if after_id is not None:
                condition += " AND m.id > ?"
                values.append(after_id)
            order = "DESC" if after_id is None else "ASC"
            values.append(limit if after_id is None else limit + 1)
            rows = connection.execute(f"""
                SELECT m.id, m.sender_id,
                    CASE WHEN u.deleted_at IS NULL THEN u.username ELSE 'deleted-user' END AS sender_username,
                    m.recipient_id, m.body, m.created_at
                FROM messages m JOIN users u ON u.id = m.sender_id
                WHERE {condition} ORDER BY m.id {order} LIMIT ?
            """, values).fetchall()
            has_more = after_id is not None and len(rows) > limit
            rows = rows[:limit]
            if after_id is None:
                rows.reverse()
            return {"messages": [dict(row) for row in rows], "has_more": has_more}

    def send(self, actor_id, body, recipient_id=None):
        if not isinstance(body, str) or not 1 <= len(body) <= 4000 or not body.strip():
            raise StoreError(422, "Message must contain 1–4,000 characters and cannot be blank.")
        if "\x00" in body:
            raise StoreError(422, "Message cannot contain a null character.")
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            self.require_active_user(connection, actor_id)
            if recipient_id is not None:
                self._ensure_peer(connection, recipient_id)
            created_at = utc_timestamp(self.clock())
            cursor = connection.execute("INSERT INTO messages(sender_id, recipient_id, body, created_at) VALUES (?, ?, ?, ?)",
                                        (actor_id, recipient_id, body, created_at))
            row = connection.execute("""
                SELECT m.id, m.sender_id,
                    CASE WHEN u.deleted_at IS NULL THEN u.username ELSE 'deleted-user' END AS sender_username,
                    m.recipient_id, m.body, m.created_at
                FROM messages m JOIN users u ON u.id = m.sender_id WHERE m.id = ?
            """, (cursor.lastrowid,)).fetchone()
            return dict(row)

    def notifications(self, actor_id, *, after_id=None, limit=100):
        """Incoming messages across all conversations, without exposing other DMs.

        Omitted cursors establish a baseline without replaying old messages. A
        resumed feed drains oldest first so more than one page cannot be lost.
        """
        if type(limit) is not int or not 1 <= limit <= 100:
            raise StoreError(422, "Notification limit must be between 1 and 100.")
        if after_id is not None and (type(after_id) is not int or not 0 <= after_id <= 2**63 - 1):
            raise StoreError(422, "Notification cursor must be a nonnegative integer.")
        condition = "m.sender_id != ? AND (m.recipient_id IS NULL OR m.recipient_id = ?)"
        with closing(self._connect()) as connection, connection:
            # The watermark and page must describe one snapshot, including when
            # another client writes a message between these queries.
            connection.execute("BEGIN")
            latest_id = connection.execute(
                f"SELECT COALESCE(MAX(m.id), 0) FROM messages m WHERE {condition}",
                (actor_id, actor_id)).fetchone()[0]
            if after_id is None:
                return {"messages": [], "cursor": latest_id, "latest_id": latest_id, "has_more": False}
            rows = connection.execute(f"""
                SELECT m.id, m.sender_id,
                    CASE WHEN u.deleted_at IS NULL THEN u.username ELSE 'deleted-user' END AS sender_username,
                    m.recipient_id, m.body, m.created_at
                FROM messages m JOIN users u ON u.id = m.sender_id
                WHERE {condition} AND m.id > ? ORDER BY m.id ASC LIMIT ?
            """, (actor_id, actor_id, after_id, limit + 1)).fetchall()
            page = rows[:limit]
            return {"messages": [dict(row) for row in page],
                    "cursor": page[-1]["id"] if page else max(after_id, latest_id),
                    "latest_id": latest_id, "has_more": len(rows) > limit}

    def mark_read(self, actor_id, *, peer_id=None, through_id, session_token=None):
        """Record a reader's opened conversation, independently of popup acknowledgements."""
        if type(through_id) is not int or not 1 <= through_id <= 2**63 - 1:
            raise StoreError(422, "Choose a valid incoming message to mark as read.")
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            self.require_active_user(connection, actor_id)
            if session_token is not None:
                self._require_receipt_session(connection, actor_id, session_token)
            if peer_id is not None:
                if type(peer_id) is not int or not 1 <= peer_id <= 2**63 - 1:
                    raise StoreError(422, "Choose a valid recipient.")
                self._ensure_peer(connection, peer_id, include_deleted=True)
            message = connection.execute("SELECT sender_id, recipient_id FROM messages WHERE id=?", (through_id,)).fetchone()
            belongs = message is not None and message["sender_id"] != actor_id
            if belongs:
                belongs = (message["recipient_id"] is None if peer_id is None else
                           message["sender_id"] == peer_id and message["recipient_id"] == actor_id)
            if not belongs:
                # Same response for missing, own, and inaccessible messages.
                raise StoreError(422, "Choose an incoming message from this conversation.")
            conversation_id = peer_id or 0
            connection.execute("""
                INSERT INTO read_receipts(reader_id, conversation_id, through_id) VALUES (?, ?, ?)
                ON CONFLICT(reader_id, conversation_id) DO UPDATE
                SET through_id=MAX(read_receipts.through_id, excluded.through_id)
            """, (actor_id, conversation_id, through_id))
            cursor = connection.execute("SELECT through_id FROM read_receipts WHERE reader_id=? AND conversation_id=?",
                                        (actor_id, conversation_id)).fetchone()[0]
            return {"peer_id": peer_id, "through_id": cursor}

    def message_receipts(self, actor_id, *, peer_id=None, after_id=0, limit=50, session_token=None):
        """Only the author can inspect a sent message's readers; names are bounded."""
        if type(after_id) is not int or not 0 <= after_id <= 2**63 - 1:
            raise StoreError(422, "Receipt cursor must be a nonnegative integer.")
        if type(limit) is not int or not 1 <= limit <= 50:
            raise StoreError(422, "Receipt limit must be between 1 and 50.")
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN")
            self.require_active_user(connection, actor_id)
            if session_token is not None:
                self._require_receipt_session(connection, actor_id, session_token)
            if peer_id is not None:
                if type(peer_id) is not int or not 1 <= peer_id <= 2**63 - 1:
                    raise StoreError(422, "Choose a valid recipient.")
                self._ensure_peer(connection, peer_id, include_deleted=True)
            messages = connection.execute("""
                SELECT id FROM messages WHERE sender_id=? AND recipient_id IS ? AND id>?
                ORDER BY id DESC LIMIT ?
            """, (actor_id, peer_id, after_id, limit)).fetchall()
            receipts = []
            for message in reversed(messages):
                condition = "r.conversation_id=0 AND r.reader_id!=?" if peer_id is None else "r.conversation_id=? AND r.reader_id=?"
                values = [actor_id] if peer_id is None else [actor_id, peer_id]
                parameters = [*values, message["id"]]
                joins = f"FROM read_receipts r JOIN users u ON u.id=r.reader_id WHERE {condition} AND r.through_id>=? AND u.deleted_at IS NULL"
                count = connection.execute("SELECT COUNT(*) " + joins, parameters).fetchone()[0]
                readers = connection.execute("SELECT u.id, u.username " + joins +
                                             " ORDER BY u.username COLLATE NOCASE LIMIT 20", parameters).fetchall()
                receipts.append({"message_id": message["id"], "read_count": count,
                                 "readers": [dict(reader) for reader in readers]})
            return {"peer_id": peer_id, "receipts": receipts}

    def _require_receipt_session(self, connection, actor_id, token):
        """Check revocation again inside the receipt transaction, after API auth."""
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise StoreError(401, "Sign in to use work chat.")
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        if connection.execute("SELECT 1 FROM sessions WHERE token_digest=? AND user_id=? AND expires_at>?",
                              (digest, actor_id, self.clock())).fetchone() is None:
            raise StoreError(401, "Your session has ended. Sign in again.")

    def generate_recovery_code(self, actor_id, password):
        """Rotate a signed-in user's recovery credential after password proof."""
        validate_password(password)
        with closing(self._connect()) as connection:
            user = connection.execute("SELECT password_hash FROM users WHERE id = ? AND deleted_at IS NULL", (actor_id,)).fetchone()
        password_hash = user["password_hash"] if user else self.dummy_hash
        if not self._verify(password_hash, password) or user is None:
            raise StoreError(401, "Password is incorrect.")
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute("SELECT password_hash FROM users WHERE id = ? AND deleted_at IS NULL", (actor_id,)).fetchone()
            if current is None or current["password_hash"] != password_hash:
                raise StoreError(401, "Password is incorrect.")
            return {"recovery_code": self._issue_recovery_code(connection, actor_id)}

    def recover_password(self, username, recovery_code, password):
        """Use a one-time recovery code, rotate it, and revoke every old session."""
        validate_username(username)
        validate_password(password)
        if not isinstance(recovery_code, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", recovery_code):
            raise StoreError(401, "Username or recovery code is incorrect.")
        digest = hashlib.sha256(recovery_code.encode("ascii")).hexdigest()
        with closing(self._connect()) as connection:
            user = connection.execute("""
                SELECT u.id, r.code_digest FROM users u
                JOIN recovery_codes r ON r.user_id = u.id WHERE u.username = ? AND u.deleted_at IS NULL
            """, (username,)).fetchone()
        expected = user["code_digest"] if user else "0" * 64
        if not secrets.compare_digest(expected, digest) or user is None:
            raise StoreError(401, "Username or recovery code is incorrect.")
        password_hash = self._hash(password)
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            # Recheck under the write lock: concurrent reset/rotation can use a
            # code only once, and administrator resets invalidate pending work.
            current = connection.execute("""SELECT r.code_digest FROM recovery_codes r JOIN users u ON u.id=r.user_id
                                         WHERE r.user_id = ? AND u.deleted_at IS NULL""",
                                         (user["id"],)).fetchone()
            if current is None or not secrets.compare_digest(current["code_digest"], digest):
                raise StoreError(401, "Username or recovery code is incorrect.")
            connection.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user["id"]))
            connection.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
            return {"recovery_code": self._issue_recovery_code(connection, user["id"])}

    def reset_password(self, username, password):
        validate_username(username)
        validate_password(password)
        password_hash = self._hash(password)
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            user = connection.execute("SELECT id FROM users WHERE username = ? AND deleted_at IS NULL", (username,)).fetchone()
            if user is None:
                raise StoreError(404, "That user was not found.")
            connection.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user["id"]))
            connection.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
            connection.execute("DELETE FROM recovery_codes WHERE user_id = ?", (user["id"],))

    def delete_account(self, actor_id, password, confirm_username):
        """Permanently remove credentials and anonymize retained company history."""
        validate_password(password)
        validate_username(confirm_username)
        with closing(self._connect()) as connection:
            user = connection.execute("SELECT username,password_hash FROM users WHERE id=? AND deleted_at IS NULL",
                                      (actor_id,)).fetchone()
        if user is None:
            raise StoreError(401, "Your session has ended. Sign in again.")
        if confirm_username != user["username"]:
            raise StoreError(422, "Type your username exactly to confirm permanent account deletion.")
        if not self._verify(user["password_hash"], password):
            raise StoreError(401, "Password is incorrect.")
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute("SELECT username,password_hash FROM users WHERE id=? AND deleted_at IS NULL",
                                         (actor_id,)).fetchone()
            if current is None or (current["username"], current["password_hash"]) != (user["username"], user["password_hash"]):
                raise StoreError(401, "Your account changed. Sign in again before deleting it.")
            tombstone = "deleted-" + secrets.token_hex(12)
            while connection.execute("SELECT 1 FROM users WHERE username=?", (tombstone,)).fetchone():
                tombstone = "deleted-" + secrets.token_hex(12)
            deleted_at = utc_timestamp(self.clock())
            connection.execute("UPDATE users SET username=?,password_hash='!deleted',deleted_at=? WHERE id=?",
                               (tombstone, deleted_at, actor_id))
            connection.execute("DELETE FROM sessions WHERE user_id=?", (actor_id,))
            connection.execute("DELETE FROM recovery_codes WHERE user_id=?", (actor_id,))
            connection.execute("DELETE FROM read_receipts WHERE reader_id=?", (actor_id,))
            # Shared stock is company data; uncommitted uploads and retry receipts
            # belong to the removed account and must not survive it.
            tables = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "inventory_imports" in tables:
                if "inventory_import_rows" in tables:
                    connection.execute("DELETE FROM inventory_import_rows WHERE import_id IN (SELECT id FROM inventory_imports WHERE user_id=?)", (actor_id,))
                connection.execute("DELETE FROM inventory_imports WHERE user_id=?", (actor_id,))
            # Advance affected company records so another PC refreshes a cached
            # author to the anonymous label without changing order contents.
            for record in connection.execute("SELECT id FROM business_entries WHERE updated_by=? ORDER BY revision", (actor_id,)).fetchall():
                connection.execute("UPDATE business_state SET revision=revision+1 WHERE id=1")
                revision = connection.execute("SELECT revision FROM business_state WHERE id=1").fetchone()[0]
                connection.execute("UPDATE business_entries SET revision=?,updated_at=? WHERE id=?",
                                   (revision, deleted_at, record["id"]))

    @staticmethod
    def _business_identifier(identifier):
        try:
            return validate_business_id(identifier)
        except BusinessValidationError as error:
            raise StoreError(422, str(error)) from None

    @staticmethod
    def _business_revision(value):
        if type(value) is not int or not 0 <= value < 2**63 - 1:
            raise StoreError(422, 'The expected revision must be a nonnegative integer.')
        return value

    @staticmethod
    def _business_record(row):
        return {'id': row['id'], 'revision': row['revision'], 'deleted': bool(row['deleted']),
                'entry': None if row['deleted'] else json.loads(row['entry_json']),
                'updated_by_username': row['updated_by_username'], 'updated_at': row['updated_at']}

    @staticmethod
    def _business_row(connection, identifier):
        return connection.execute('''
            SELECT b.id, b.revision, b.deleted, b.entry_json,
                CASE WHEN u.deleted_at IS NULL THEN u.username ELSE 'deleted-user' END AS updated_by_username, b.updated_at
            FROM business_entries b JOIN users u ON u.id = b.updated_by WHERE b.id = ?
        ''', (identifier,)).fetchone()

    def business_entries(self, *, after_revision=0, limit=3):
        self._business_revision(after_revision)
        if type(limit) is not int or not 1 <= limit <= 3:
            raise StoreError(422, 'Shared business page size must be from 1 to 3.')
        with closing(self._connect()) as connection:
            rows = connection.execute('''
                SELECT b.id, b.revision, b.deleted, b.entry_json,
                    CASE WHEN u.deleted_at IS NULL THEN u.username ELSE 'deleted-user' END AS updated_by_username, b.updated_at
                FROM business_entries b JOIN users u ON u.id = b.updated_by
                WHERE b.revision > ? ORDER BY b.revision LIMIT ?
            ''', (after_revision, limit + 1)).fetchall()
            page = rows[:limit]
            return {'entries': [self._business_record(row) for row in page],
                    'cursor': page[-1]['revision'] if page else after_revision, 'has_more': len(rows) > limit}

    def save_business_entry(self, actor_id, identifier, expected_revision, entry):
        self._business_identifier(identifier)
        self._business_revision(expected_revision)
        now = self.clock()
        try:
            canonical = validate_business_entry(entry, identifier, now=now)
        except BusinessValidationError as error:
            raise StoreError(422, str(error)) from None
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            self.require_active_user(connection, actor_id)
            existing = self._business_row(connection, identifier)
            if (existing['revision'] if existing else 0) != expected_revision:
                raise StoreError(409, 'This entry changed on another computer. Reload it before saving your changes.')
            if existing and existing['deleted']:
                raise StoreError(409, 'This shared entry was deleted. Create a new entry instead.')
            if existing is None:
                count = connection.execute('SELECT COUNT(*) FROM business_entries WHERE deleted = 0').fetchone()[0]
                if count >= self.max_business_entries:
                    raise StoreError(409, 'The shared notebook is full. Back up and delete older entries before adding more.')
            else:
                previous = json.loads(existing['entry_json'])
                canonical['created'] = previous['created']
                canonical['written'] = now if (previous['title'], previous['body']) != (canonical['title'], canonical['body']) else previous['written']
            # The authenticated server actor owns attribution; submitted names
            # cannot forge authorship or replace an existing row's creator.
            previous_rows = {row['id']: row for row in previous.get('checklist', [])} if existing else {}
            actor_name = connection.execute('SELECT username FROM users WHERE id=?', (actor_id,)).fetchone()[0]
            canonical.pop('added_by', None)
            canonical.pop('added_at', None)
            if existing:
                for key in ('added_by', 'added_at'):
                    if key in previous:
                        canonical[key] = previous[key]
            else:
                canonical.update(added_by=actor_name, added_at=now)
            for row in canonical['checklist']:
                old_row = previous_rows.get(row['id'])
                row.pop('added_by', None)
                row.pop('added_at', None)
                if old_row is None:
                    row.update(added_by=actor_name, added_at=now)
                else:
                    for key in ('added_by', 'added_at'):
                        if key in old_row:
                            row[key] = old_row[key]
            canonical['updated'] = now
            connection.execute('UPDATE business_state SET revision = revision + 1 WHERE id = 1')
            revision = connection.execute('SELECT revision FROM business_state WHERE id = 1').fetchone()[0]
            connection.execute('''
                INSERT INTO business_entries(id, revision, deleted, entry_json, updated_by, updated_at)
                VALUES (?, ?, 0, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET revision=excluded.revision, deleted=0,
                    entry_json=excluded.entry_json, updated_by=excluded.updated_by, updated_at=excluded.updated_at
            ''', (identifier, revision, json.dumps(canonical, ensure_ascii=False, separators=(',', ':')),
                  actor_id, utc_timestamp(now)))
            return self._business_record(self._business_row(connection, identifier))

    def delete_business_entry(self, actor_id, identifier, expected_revision):
        self._business_identifier(identifier)
        self._business_revision(expected_revision)
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            self.require_active_user(connection, actor_id)
            existing = self._business_row(connection, identifier)
            if existing is None:
                raise StoreError(404, 'That shared entry was not found.')
            if existing['revision'] != expected_revision:
                raise StoreError(409, 'This entry changed on another computer. Reload it before deleting.')
            if existing['deleted']:
                raise StoreError(409, 'This shared entry was already deleted.')
            connection.execute('UPDATE business_state SET revision = revision + 1 WHERE id = 1')
            revision = connection.execute('SELECT revision FROM business_state WHERE id = 1').fetchone()[0]
            connection.execute('UPDATE business_entries SET revision=?, deleted=1, entry_json=NULL, updated_by=?, updated_at=? WHERE id=?',
                               (revision, actor_id, utc_timestamp(self.clock()), identifier))
            return self._business_record(self._business_row(connection, identifier))
