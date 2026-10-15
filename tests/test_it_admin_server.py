"""IT privilege isolation, one-time local setup, and transactional account controls."""
from __future__ import annotations

import asyncio
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
SERVER_AVAILABLE = all(importlib.util.find_spec(module) for module in ("fastapi", "argon2"))
if SERVER_AVAILABLE:
    from work_server.app import create_app
    from work_server.admin_store import ITAdminStore
    from work_server.store import ChatStore, StoreError

PASSWORD = "a secure IT passphrase"
EMPLOYEE_PASSWORD = "a secure employee password"


@unittest.skipUnless(SERVER_AVAILABLE, "Install requirements-server.txt")
class ITAdminServerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "work.sqlite"
        self.now = [1_800_000_000.0]
        self.app = create_app(self.database, clock=lambda: self.now[0], allow_lan_http=True)
        self.store = self.app.state.store
        self.admin = self.app.state.it_admin

    def request(self, path, *, method="GET", data=None, token=None, client="127.0.0.1", headers=None):
        async def run():
            raw = json.dumps(data).encode() if data is not None else b""
            pairs = [(b"host", b"office:8765"), (b"content-type", b"application/json")]
            if token is not None:
                pairs.append((b"authorization", ("Bearer " + token).encode()))
            pairs.extend((name.lower().encode(), value.encode()) for name, value in (headers or {}).items())
            scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                     "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
                     "root_path": "", "query_string": b"", "client": (client, 12345),
                     "server": ("127.0.0.1", 8765), "headers": pairs}
            sent = []

            async def receive():
                return {"type": "http.request", "body": raw, "more_body": False}

            async def send(event):
                sent.append(event)

            await self.app(scope, receive, send)
            response = b"".join(event.get("body", b"") for event in sent if event["type"] == "http.response.body")
            return sent[0]["status"], json.loads(response) if response else None, dict(sent[0]["headers"])
        return asyncio.run(run())

    def login(self, password="admin123", **kwargs):
        return self.request("/api/it/login", method="POST", data={"username": "admin", "password": password}, **kwargs)

    def ready(self):
        bootstrap = self.admin.login("admin", "admin123", local=True)
        return self.admin.change_password(bootstrap["token"], "admin123", PASSWORD, local=True)

    def employee(self, name="employee"):
        return self.store.register(name, EMPLOYEE_PASSWORD)

    def test_bootstrap_requires_socket_loopback_and_rejects_forwarded_spoof(self):
        for client in ("192.168.1.10", "::ffff:192.168.1.10"):
            status, result, _ = self.login(client=client, headers={"X-Forwarded-For": "127.0.0.1", "Forwarded": "for=127.0.0.1"})
            self.assertEqual(status, 403)
            self.assertIn("localhost", result["detail"])
        for client in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            status, result, _ = self.login(client=client)
            self.assertEqual(status, 200)
            self.assertTrue(result["must_change_password"])
            self.assertTrue(result["token"].startswith("it_"))

    def test_bootstrap_can_only_change_password_or_log_out_locally(self):
        bootstrap = self.login()[1]
        for path in ("/api/it/overview", "/api/it/users"):
            status, result, _ = self.request(path, token=bootstrap["token"])
            self.assertEqual(status, 403)
            self.assertIn("Change the initial", result["detail"])
        employee = self.employee()
        status, _, _ = self.request(f"/api/it/users/{employee['user']['id']}/sign-out", method="POST", token=bootstrap["token"],
                                   data={"admin_password": PASSWORD, "confirm_username": "employee"})
        self.assertEqual(status, 403)
        status, _, _ = self.request("/api/it/password", method="POST", token=bootstrap["token"], client="192.168.1.10",
                                   data={"current_password": "admin123", "new_password": PASSWORD})
        self.assertEqual(status, 403)
        self.assertEqual(self.request("/api/it/logout", method="POST", token=bootstrap["token"], data={})[0], 204)
        self.assertEqual(self.request("/api/it/password", method="POST", token=bootstrap["token"],
                                     data={"current_password": "admin123", "new_password": PASSWORD})[0], 401)

    def test_rotation_revokes_every_bootstrap_session_and_survives_restart(self):
        first, second = self.login()[1], self.login()[1]
        status, session, _ = self.request("/api/it/password", method="POST", token=first["token"],
                                        data={"current_password": "admin123", "new_password": PASSWORD})
        self.assertEqual(status, 200)
        self.assertFalse(session["must_change_password"])
        self.assertEqual(session["username"], "admin")
        self.assertTrue(session["expires_at"].endswith("Z"))
        for old in (first, second):
            self.assertEqual(self.request("/api/it/overview", token=old["token"])[0], 401)
        self.assertEqual(self.login("admin123")[0], 401)
        self.assertEqual(self.login(PASSWORD, client="192.168.1.10")[0], 200)
        with patch.object(self.store, "_hash", side_effect=AssertionError("must not reseed on restart")):
            reopened = ITAdminStore(self.store)
        self.assertEqual(reopened.overview(session["token"])["users"], 0)
        self.assertFalse(reopened.login("admin", PASSWORD)["must_change_password"])

    def test_it_and_employee_tokens_have_no_cross_privileges_even_employee_named_admin(self):
        employee = self.employee("admin")
        admin = self.ready()
        for path in ("/api/it/overview", "/api/it/users"):
            self.assertEqual(self.request(path, token=employee["token"])[0], 401)
        for path in ("/api/me", "/api/messages", "/api/users", "/api/inventory/summary", "/api/business"):
            self.assertEqual(self.request(path, token=admin["token"])[0], 401)
        self.assertEqual(self.login(EMPLOYEE_PASSWORD)[0], 401)
        self.assertEqual(self.store.authenticate(employee["token"])["username"], "admin")

    def test_bootstrap_ten_minute_and_regular_one_hour_expiry(self):
        bootstrap = self.admin.login("admin", "admin123", local=True)
        self.now[0] += 600
        with self.assertRaises(StoreError) as error:
            self.admin.change_password(bootstrap["token"], "admin123", PASSWORD, local=True)
        self.assertEqual(error.exception.status, 401)
        session = self.ready()
        self.now[0] += 3599
        self.assertEqual(self.admin.overview(session["token"])["users"], 0)
        self.now[0] += 1
        self.assertEqual(self.request("/api/it/overview", token=session["token"])[0], 401)

    def test_strict_password_inputs_never_echo_credentials_or_allow_initial_again(self):
        session = self.ready()
        for new in ("admin123", "short", "x" * 129, 12345):
            status, body, _ = self.request("/api/it/password", method="POST", token=session["token"],
                                         data={"current_password": PASSWORD, "new_password": new})
            self.assertEqual(status, 422)
            self.assertNotIn(PASSWORD, json.dumps(body))
        status, body, _ = self.request("/api/it/login", method="POST",
                                     data={"username": "admin", "password": PASSWORD, "role": "admin"})
        self.assertEqual(status, 422)
        self.assertNotIn(PASSWORD, json.dumps(body))
        self.assertEqual(self.request("/api/it/password", method="POST", token=session["token"],
            data={"current_password": "incorrect old password", "new_password": "replacement password"})[0], 401)
        self.assertEqual(self.request("/api/it/password", method="POST", token=session["token"],
            data={"current_password": PASSWORD, "new_password": PASSWORD})[0], 422)

    def test_employee_reset_revokes_sessions_and_recovery_and_preserves_other_employee(self):
        session = self.ready()
        employee, other = self.employee(), self.employee("another_employee")
        second = self.store.login("employee", EMPLOYEE_PASSWORD)
        self.store.send(employee["user"]["id"], "Retain company chat")
        status, _, _ = self.request(f"/api/it/users/{employee['user']['id']}/reset-password", method="POST", token=session["token"],
            data={"admin_password": PASSWORD, "confirm_username": "employee", "new_password": "new employee password"})
        self.assertEqual(status, 204)
        for old in (employee, second):
            self.assertEqual(self.request("/api/me", token=old["token"])[0], 401)
        self.assertEqual(self.request("/api/me", token=other["token"])[0], 200)
        with self.assertRaises(StoreError):
            self.store.recover_password("employee", employee["recovery_code"], "another employee password")
        restored = self.store.login("employee", "new employee password")
        self.assertEqual(restored["user"], employee["user"])
        self.assertEqual(self.admin.overview(session["token"])["messages"], 1)

    def test_sign_out_does_not_change_password_or_recovery(self):
        session, employee = self.ready(), self.employee()
        status, _, _ = self.request(f"/api/it/users/{employee['user']['id']}/sign-out", method="POST", token=session["token"],
                                   data={"admin_password": PASSWORD, "confirm_username": "employee"})
        self.assertEqual(status, 204)
        self.assertEqual(self.request("/api/me", token=employee["token"])[0], 401)
        self.assertTrue(self.store.login("employee", EMPLOYEE_PASSWORD)["token"])
        self.assertTrue(self.store.recover_password("employee", employee["recovery_code"], "new employee password")["recovery_code"])

    def test_employee_action_requires_password_exact_name_active_target_and_valid_id(self):
        session, employee = self.ready(), self.employee()
        path = f"/api/it/users/{employee['user']['id']}/sign-out"
        for password, name, expected in (("wrong IT password", "employee", 401), (PASSWORD, "Employee", 422), (PASSWORD, "another_employee", 422)):
            self.assertEqual(self.request(path, method="POST", token=session["token"],
                data={"admin_password": password, "confirm_username": name})[0], expected)
        for identifier in (0, -1, 2**63):
            self.assertEqual(self.request(f"/api/it/users/{identifier}/sign-out", method="POST", token=session["token"],
                data={"admin_password": PASSWORD, "confirm_username": "employee"})[0], 422)
        self.store.delete_account(employee["user"]["id"], EMPLOYEE_PASSWORD, "employee")
        self.assertEqual(self.request(path, method="POST", token=session["token"],
            data={"admin_password": PASSWORD, "confirm_username": "employee"})[0], 404)
        self.assertEqual(self.admin.users(session["token"])["users"], [])

    def test_dashboard_exposes_counts_and_bounded_audit_without_chat_or_credentials(self):
        session, employee = self.ready(), self.employee()
        self.store.send(employee["user"]["id"], "PRIVATE CHAT CONTENT", employee["user"]["id"])
        with closing(self.store._connect()) as connection, connection:
            connection.execute("UPDATE sessions SET expires_at=?", (self.now[0],))
            for _index in range(110):
                self.admin._audit(connection, "employee_sessions_revoked", "employee")
        result = self.request("/api/it/overview", token=session["token"])[1]
        self.assertEqual((result["users"], result["sessions"], result["messages"], result["inventory_items"]), (1, 0, 1, 0))
        self.assertEqual(len(result["audit"]), 100)
        self.assertEqual(set(result["audit"][0]), {"created_at", "action", "target_username"})
        directory = self.request("/api/it/users", token=session["token"])[1]
        self.assertEqual(set(directory["users"][0]), {"id", "username", "created_at", "active_sessions"})
        self.assertEqual(directory["users"][0]["active_sessions"], 0)
        for secret in ("PRIVATE CHAT CONTENT", PASSWORD, EMPLOYEE_PASSWORD, employee["token"], session["token"], "password_hash"):
            self.assertNotIn(secret, json.dumps([result, directory]))

    def test_credentials_hashed_in_separate_tables_and_only_token_digests_saved(self):
        session = self.ready()
        with closing(self.store._connect()) as connection:
            row = connection.execute("SELECT * FROM it_admin").fetchone()
            self.assertTrue(row["password_hash"].startswith("$argon2id$"))
            self.assertNotIn(PASSWORD, row["password_hash"])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)
            digest = connection.execute("SELECT token_digest FROM it_admin_sessions").fetchone()[0]
            self.assertEqual(len(digest), 64)
            self.assertNotEqual(digest, session["token"])

    def test_rate_limits_apply_before_password_hash_work_and_have_retry_after(self):
        self.app.state.it_auth_limiter.maximum = 1
        self.assertEqual(self.login("incorrect passphrase")[0], 401)
        with patch.object(self.store, "_verify", side_effect=AssertionError("must not hash")):
            status, _, headers = self.login()
        self.assertEqual(status, 429)
        self.assertEqual(headers[b"retry-after"], b"60")
        self.app.state.it_auth_limiter.attempts.clear()
        self.app.state.it_account_limiter.maximum = 1
        with patch.object(self.store, "_verify", side_effect=AssertionError("must not hash")):
            self.assertEqual(self.login(client="::1")[0], 429)

    def test_login_inflight_password_rotation_cannot_issue_old_credential_session(self):
        session = self.ready()
        original = self.store._verify
        changed = []

        def verify_then_rotate(password_hash, password):
            result = original(password_hash, password)
            if not changed:
                changed.append(True)
                self.admin.reset_local_password("new emergency IT password")
            return result

        with patch.object(self.store, "_verify", side_effect=verify_then_rotate), self.assertRaises(StoreError) as error:
            self.admin.login("admin", PASSWORD)
        self.assertEqual(error.exception.status, 401)
        with self.assertRaises(StoreError):
            self.admin.overview(session["token"])

    def test_employee_action_cannot_finish_after_admin_session_logout(self):
        session, employee = self.ready(), self.employee()
        original = self.store._verify

        def verify_then_logout(password_hash, password):
            result = original(password_hash, password)
            self.admin.logout(session["token"])
            return result

        with patch.object(self.store, "_verify", side_effect=verify_then_logout), self.assertRaises(StoreError) as error:
            self.admin.employee_action(session["token"], employee["user"]["id"], PASSWORD, "employee", new_password="new employee password")
        self.assertEqual(error.exception.status, 401)
        self.assertEqual(self.store.authenticate(employee["token"])["username"], "employee")
        self.assertTrue(self.store.login("employee", EMPLOYEE_PASSWORD))

    def test_password_rotation_cannot_finish_after_logout_during_hash(self):
        session = self.ready()
        original = self.store._hash

        def hash_then_logout(password):
            result = original(password)
            self.admin.logout(session["token"])
            return result

        with patch.object(self.store, "_hash", side_effect=hash_then_logout), self.assertRaises(StoreError) as error:
            self.admin.change_password(session["token"], PASSWORD, "replacement IT password")
        self.assertEqual(error.exception.status, 401)
        self.assertTrue(self.admin.login("admin", PASSWORD)["token"])

    def test_expiry_rechecked_after_hash_before_employee_mutation(self):
        session, employee = self.ready(), self.employee()
        original = self.store._hash

        def hash_then_expire(password):
            result = original(password)
            self.now[0] += 3600
            return result

        with patch.object(self.store, "_hash", side_effect=hash_then_expire), self.assertRaises(StoreError) as error:
            self.admin.employee_action(session["token"], employee["user"]["id"], PASSWORD, "employee", new_password="new employee password")
        self.assertEqual(error.exception.status, 401)
        self.assertTrue(self.store.login("employee", EMPLOYEE_PASSWORD))

    def test_audit_and_password_reset_are_one_atomic_transaction(self):
        session, employee = self.ready(), self.employee()
        with patch.object(self.admin, "_audit", side_effect=sqlite3.OperationalError("audit unavailable")), self.assertRaises(sqlite3.OperationalError):
            self.admin.employee_action(session["token"], employee["user"]["id"], PASSWORD, "employee", new_password="new employee password")
        self.assertEqual(self.store.authenticate(employee["token"])["username"], "employee")
        self.assertTrue(self.store.login("employee", EMPLOYEE_PASSWORD))
        self.assertTrue(self.store.recover_password("employee", employee["recovery_code"], "recovery still works"))

    def test_local_cli_recovery_uses_getpass_and_revokes_only_it_sessions(self):
        from work_server.__main__ import main
        session, employee = self.ready(), self.employee()
        with patch("work_server.__main__.getpass.getpass", side_effect=["local recovery password"] * 2) as prompt, patch("builtins.print"):
            main(["reset-it-password", "--database", str(self.database)])
        self.assertEqual(prompt.call_count, 2)
        self.assertEqual(self.request("/api/it/overview", token=session["token"])[0], 401)
        self.assertEqual(self.request("/api/me", token=employee["token"])[0], 200)
        self.assertEqual(self.login("local recovery password")[0], 200)
        self.assertEqual(self.login("admin123")[0], 401)
        self.assertEqual(self.admin.overview(self.login("local recovery password")[1]["token"])["audit"][1]["action"], "login")

    def test_local_cli_refuses_missing_database_and_initial_password(self):
        from work_server.__main__ import main
        missing = Path(self.directory.name) / "missing.sqlite"
        with patch("sys.stderr"), patch("work_server.__main__.getpass.getpass") as prompt, self.assertRaises(SystemExit):
            main(["reset-it-password", "--database", str(missing)])
        prompt.assert_not_called()
        self.assertFalse(missing.exists())
        session = self.ready()
        for answers in (("admin123", "admin123"), ("new valid IT password", "different IT password")):
            with patch("sys.stderr"), patch("work_server.__main__.getpass.getpass", side_effect=answers), self.assertRaises(SystemExit):
                main(["reset-it-password", "--database", str(self.database)])
        self.assertEqual(self.request("/api/it/overview", token=session["token"])[0], 200)


if __name__ == "__main__":
    unittest.main()
