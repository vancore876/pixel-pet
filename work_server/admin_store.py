"""Separate, revocable IT identity; employee accounts never imply administration."""
from __future__ import annotations

from contextlib import closing
import hashlib
import re
import secrets

from .store import StoreError, utc_timestamp, validate_password, validate_username


ADMIN_SESSION_SECONDS = 60 * 60
BOOTSTRAP_SESSION_SECONDS = 10 * 60
TOKEN_PATTERN = re.compile(r"it_[A-Za-z0-9_-]{43}\Z")


class ITAdminStore:
    """Share SQLite and bounded Argon2 work, never employee sessions or identities."""

    def __init__(self, store):
        self.store = store
        with closing(store._connect()) as connection, connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS it_admin (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    password_hash TEXT NOT NULL,
                    must_change_password INTEGER NOT NULL CHECK(must_change_password IN (0,1))
                );
                CREATE TABLE IF NOT EXISTS it_admin_sessions (
                    token_digest TEXT PRIMARY KEY,
                    expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS it_admin_session_expiry ON it_admin_sessions(expires_at);
                CREATE TABLE IF NOT EXISTS it_admin_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target_username TEXT NOT NULL
                );
            """)
            exists = connection.execute("SELECT 1 FROM it_admin WHERE id=1").fetchone()
        if exists is None:
            password_hash = store._hash("admin123")
            with closing(store._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                inserted = connection.execute("INSERT OR IGNORE INTO it_admin VALUES(1,?,1)", (password_hash,))
                if inserted.rowcount:
                    self._audit(connection, "initialized")

    def _audit(self, connection, action, target_username="admin"):
        connection.execute("INSERT INTO it_admin_audit(created_at,action,target_username) VALUES(?,?,?)",
                           (utc_timestamp(self.store.clock()), action, target_username))

    @staticmethod
    def _password(password, *, new=False):
        if new:
            validate_password(password)
        elif not isinstance(password, str) or not 1 <= len(password) <= 128:
            raise StoreError(422, "Enter a password of at most 128 characters.")
        return password

    @staticmethod
    def _digest(token):
        if not isinstance(token, str) or not TOKEN_PATTERN.fullmatch(token):
            raise StoreError(401, "Sign in to the IT section.")
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    @staticmethod
    def _check_bootstrap(row, *, local, allow_bootstrap):
        if row["must_change_password"]:
            if not allow_bootstrap:
                raise StoreError(403, "Change the initial IT password before using administration tools.")
            if not local:
                raise StoreError(403, "First-time IT setup must be completed on the office server using localhost.")

    def _require_session(self, connection, token, *, local=False, allow_bootstrap=False):
        row = connection.execute("""
            SELECT a.password_hash,a.must_change_password FROM it_admin a
            JOIN it_admin_sessions s ON a.id=1
            WHERE s.token_digest=? AND s.expires_at>?
        """, (self._digest(token), self.store.clock())).fetchone()
        if row is None:
            raise StoreError(401, "Your IT session has ended. Sign in again.")
        self._check_bootstrap(row, local=local, allow_bootstrap=allow_bootstrap)
        return row

    def _issue_session(self, connection, must_change):
        token = "it_" + secrets.token_urlsafe(32)
        now = self.store.clock()
        expires_at = now + (BOOTSTRAP_SESSION_SECONDS if must_change else ADMIN_SESSION_SECONDS)
        connection.execute("DELETE FROM it_admin_sessions WHERE expires_at<=?", (now,))
        connection.execute("INSERT INTO it_admin_sessions VALUES(?,?)", (self._digest(token), expires_at))
        return {"token": token, "username": "admin", "must_change_password": bool(must_change),
                "expires_at": utc_timestamp(expires_at)}

    def login(self, username, password, *, local=False):
        validate_username(username)
        self._password(password)
        with closing(self.store._connect()) as connection:
            admin = connection.execute("SELECT * FROM it_admin WHERE id=1").fetchone()
        self._check_bootstrap(admin, local=local, allow_bootstrap=True)
        password_hash = admin["password_hash"] if username.casefold() == "admin" else self.store.dummy_hash
        if not self.store._verify(password_hash, password) or username.casefold() != "admin":
            raise StoreError(401, "IT username or password is incorrect.")
        with closing(self.store._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute("SELECT * FROM it_admin WHERE id=1").fetchone()
            if current["password_hash"] != password_hash:
                raise StoreError(401, "IT username or password is incorrect.")
            self._check_bootstrap(current, local=local, allow_bootstrap=True)
            session = self._issue_session(connection, current["must_change_password"])
            self._audit(connection, "login")
            return session

    def _prove_password(self, token, password, *, local=False, allow_bootstrap=False):
        self._password(password)
        with closing(self.store._connect()) as connection:
            row = self._require_session(connection, token, local=local, allow_bootstrap=allow_bootstrap)
        if not self.store._verify(row["password_hash"], password):
            raise StoreError(401, "The current IT password is incorrect.")
        return row["password_hash"]

    def _recheck_proof(self, connection, token, expected_hash, *, local=False, allow_bootstrap=False):
        row = self._require_session(connection, token, local=local, allow_bootstrap=allow_bootstrap)
        if row["password_hash"] != expected_hash:
            raise StoreError(401, "The IT password changed. Sign in again.")

    def change_password(self, token, current_password, new_password, *, local=False):
        self._password(new_password, new=True)
        expected_hash = self._prove_password(token, current_password, local=local, allow_bootstrap=True)
        if current_password == new_password:
            raise StoreError(422, "Choose a different IT password.")
        password_hash = self.store._hash(new_password)
        with closing(self.store._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            self._recheck_proof(connection, token, expected_hash, local=local, allow_bootstrap=True)
            connection.execute("UPDATE it_admin SET password_hash=?,must_change_password=0 WHERE id=1", (password_hash,))
            connection.execute("DELETE FROM it_admin_sessions")
            self._audit(connection, "password_changed")
            return self._issue_session(connection, False)

    def reset_local_password(self, new_password):
        """Emergency CLI recovery, authorized by access to the server's local DB."""
        self._password(new_password, new=True)
        password_hash = self.store._hash(new_password)
        with closing(self.store._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("UPDATE it_admin SET password_hash=?,must_change_password=0 WHERE id=1", (password_hash,))
            connection.execute("DELETE FROM it_admin_sessions")
            self._audit(connection, "local_password_reset")

    def logout(self, token, *, local=False):
        with closing(self.store._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_session(connection, token, local=local, allow_bootstrap=True)
            connection.execute("DELETE FROM it_admin_sessions WHERE token_digest=?", (self._digest(token),))
            self._audit(connection, "logout")

    def overview(self, token):
        with closing(self.store._connect()) as connection, connection:
            connection.execute("BEGIN")
            self._require_session(connection, token)
            result = {
                "users": connection.execute("SELECT COUNT(*) FROM users WHERE deleted_at IS NULL").fetchone()[0],
                "sessions": connection.execute("SELECT COUNT(*) FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.expires_at>? AND u.deleted_at IS NULL", (self.store.clock(),)).fetchone()[0],
                "messages": connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
                "inventory_items": 0,
                "audit": [dict(row) for row in connection.execute("SELECT created_at,action,target_username FROM it_admin_audit ORDER BY id DESC LIMIT 100")],
            }
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='inventory_items'").fetchone():
                result["inventory_items"] = connection.execute("SELECT COUNT(*) FROM inventory_items").fetchone()[0]
            return result

    def users(self, token):
        with closing(self.store._connect()) as connection, connection:
            connection.execute("BEGIN")
            self._require_session(connection, token)
            rows = connection.execute("""
                SELECT u.id,u.username,u.created_at,COUNT(s.token_digest) AS active_sessions
                FROM users u LEFT JOIN sessions s ON s.user_id=u.id AND s.expires_at>?
                WHERE u.deleted_at IS NULL GROUP BY u.id
                ORDER BY u.username COLLATE NOCASE LIMIT ?
            """, (self.store.clock(), self.store.max_users + 1)).fetchall()
            if len(rows) > self.store.max_users:
                raise StoreError(503, "The employee directory exceeds this server's account capacity.")
            return {"users": [dict(row) for row in rows]}

    def employee_action(self, token, identifier, admin_password, confirm_username, *, new_password=None):
        if type(identifier) is not int or not 1 <= identifier < 2**63:
            raise StoreError(422, "Choose a valid employee account.")
        validate_username(confirm_username)
        if new_password is not None:
            self._password(new_password, new=True)
        expected_hash = self._prove_password(token, admin_password)
        password_hash = self.store._hash(new_password) if new_password is not None else None
        with closing(self.store._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            self._recheck_proof(connection, token, expected_hash)
            user = connection.execute("SELECT username FROM users WHERE id=? AND deleted_at IS NULL", (identifier,)).fetchone()
            if user is None:
                raise StoreError(404, "That employee account was not found.")
            if user["username"] != confirm_username:
                raise StoreError(422, "Type the employee's username exactly to confirm.")
            if password_hash is not None:
                connection.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, identifier))
                connection.execute("DELETE FROM recovery_codes WHERE user_id=?", (identifier,))
            connection.execute("DELETE FROM sessions WHERE user_id=?", (identifier,))
            self._audit(connection, "employee_password_reset" if password_hash is not None else "employee_sessions_revoked", user["username"])
