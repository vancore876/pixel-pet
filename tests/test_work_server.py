"""Optional server tests, including real HTTP authentication and message privacy."""
from __future__ import annotations

import asyncio
from contextlib import closing
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
SERVER_AVAILABLE = all(importlib.util.find_spec(module) for module in ("fastapi", "uvicorn", "argon2"))
if SERVER_AVAILABLE:
    import uvicorn
    from work_server.app import RequestGuard, RateLimiter, create_app, is_lan_address
    from work_server.store import ChatStore, StoreError


PASSWORD = "correct horse battery staple"


@unittest.skipUnless(SERVER_AVAILABLE, "Optional backend dependencies: install requirements-server.txt")
class WorkServerHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.database = Path(cls.directory.name) / "chat.sqlite"
        cls.now = [1_800_000_000.0]
        cls.app = create_app(cls.database, clock=lambda: cls.now[0], auth_limit=1000,
                             send_limit=1000, account_auth_limit=1000)
        cls.socket = socket.socket()
        cls.socket.bind(("127.0.0.1", 0))
        cls.address = "http://127.0.0.1:" + str(cls.socket.getsockname()[1])
        cls.server = uvicorn.Server(uvicorn.Config(cls.app, host="127.0.0.1", log_level="error",
                                                  proxy_headers=False, access_log=False))
        cls.thread = threading.Thread(target=cls.server.run, kwargs={"sockets": [cls.socket]}, daemon=True)
        cls.thread.start()
        deadline = time.monotonic() + 10
        while not cls.server.started and cls.thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not cls.server.started:
            raise RuntimeError("Loopback test server did not start")
        cls.user_counter = 0

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(timeout=10)
        cls.socket.close()
        cls.directory.cleanup()

    def request(self, path, *, method="GET", data=None, token=None, headers=None, raw=None):
        merged = dict(headers or {})
        if token:
            merged["Authorization"] = "Bearer " + token
        if data is not None:
            raw = json.dumps(data).encode("utf-8")
            merged["Content-Type"] = "application/json"
        request = Request(self.address + path, data=raw, headers=merged, method=method)
        try:
            response = urlopen(request, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            payload = response.read()
            content_type = response.headers.get("Content-Type", "")
            result = json.loads(payload) if payload and "application/json" in content_type else payload
            return response.status, result, response.headers

    def register(self, password=PASSWORD):
        type(self).user_counter += 1
        username = "staff_" + str(self.user_counter)
        status, account, _ = self.request("/api/auth/register", method="POST",
                                           data={"username": username, "password": password})
        self.assertEqual(status, 201, account)
        return account

    def test_health_headers_and_docs_are_private(self):
        self.assertFalse(self.app._native_telemetry.enabled())
        status, payload, headers = self.request("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"status": "ok", "service": "Jeffery Work Chat", "version": 1})
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        for path in ("/docs", "/redoc", "/openapi.json"):
            self.assertEqual(self.request(path)[0], 404)

    def test_requires_session_for_every_chat_operation(self):
        for path in ("/api/me", "/api/users", "/api/messages"):
            self.assertEqual(self.request(path)[0], 401)
        self.assertEqual(self.request("/api/messages", method="POST", data={"body": "secret"})[0], 401)
        self.assertEqual(self.request("/api/auth/logout", method="POST")[0], 401)

    def test_register_case_insensitive_login_and_storage_secrets(self):
        account = self.register()
        self.assertGreaterEqual(len(account["token"]), 43)
        self.assertTrue(account["expires_at"].endswith("Z"))
        status, logged_in, _ = self.request("/api/auth/login", method="POST",
            data={"username": account["user"]["username"].upper(), "password": PASSWORD})
        self.assertEqual(status, 200)
        self.assertEqual(logged_in["user"], account["user"])
        self.assertNotEqual(logged_in["token"], account["token"])
        self.assertEqual(self.request("/api/me", token=account["token"])[1], account["user"])
        status, _, _ = self.request("/api/auth/register", method="POST",
            data={"username": account["user"]["username"].upper(), "password": PASSWORD})
        self.assertEqual(status, 409)
        with closing(sqlite3.connect(self.database)) as connection:
            password_hash = connection.execute("SELECT password_hash FROM users WHERE id=?", (account["user"]["id"],)).fetchone()[0]
            digest = connection.execute("SELECT token_digest FROM sessions WHERE token_digest=?",
                (hashlib.sha256(account["token"].encode()).hexdigest(),)).fetchone()[0]
        self.assertTrue(password_hash.startswith("$argon2id$"))
        self.assertNotIn(PASSWORD, password_hash)
        self.assertNotEqual(digest, account["token"])

    def test_password_spaces_are_preserved_and_login_failure_is_generic(self):
        password = "  spaces are intentional  "
        account = self.register(password)
        username = account["user"]["username"]
        self.assertEqual(self.request("/api/auth/login", method="POST", data={"username": username, "password": password})[0], 200)
        wrong = self.request("/api/auth/login", method="POST", data={"username": username, "password": password.strip()})
        unknown = self.request("/api/auth/login", method="POST", data={"username": "no_such_account", "password": password})
        self.assertEqual(wrong[:2], unknown[:2])
        self.assertEqual(wrong[0], 401)

    def test_logout_and_expiration_revoke_sessions(self):
        account = self.register()
        self.assertEqual(self.request("/api/auth/logout", method="POST", token=account["token"])[0], 204)
        self.assertEqual(self.request("/api/me", token=account["token"])[0], 401)
        account = self.register()
        initial = self.now[0]
        try:
            self.now[0] += 8 * 60 * 60 + 1
            self.assertEqual(self.request("/api/me", token=account["token"])[0], 401)
        finally:
            self.now[0] = initial

    def test_team_messages_and_private_direct_messages(self):
        alice, bob, charlie = self.register(), self.register(), self.register()
        status, team, _ = self.request("/api/messages", method="POST", token=alice["token"],
                                       data={"body": "Team announcement"})
        self.assertEqual(status, 201)
        self.assertIsNone(team["message"]["recipient_id"])
        self.assertEqual(team["message"]["sender_id"], alice["user"]["id"])
        self.assertEqual(team["message"]["sender_username"], alice["user"]["username"])
        self.assertTrue(team["message"]["created_at"].endswith("Z"))
        status, direct, _ = self.request("/api/messages", method="POST", token=alice["token"],
            data={"recipient_id": bob["user"]["id"], "body": "Only for Bob"})
        self.assertEqual(status, 201)
        private_id = direct["message"]["id"]
        for reader, peer in ((alice, bob), (bob, alice)):
            payload = self.request("/api/messages?peer_id=" + str(peer["user"]["id"]), token=reader["token"])[1]
            self.assertEqual([message["id"] for message in payload["messages"]], [private_id])
        for peer in (alice, bob):
            payload = self.request("/api/messages?peer_id=" + str(peer["user"]["id"]), token=charlie["token"])[1]
            self.assertEqual(payload["messages"], [])
        for reader in (bob, charlie):
            payload = self.request("/api/messages?after_id=" + str(team["message"]["id"] - 1), token=reader["token"])[1]
            self.assertIn(team["message"]["id"], [message["id"] for message in payload["messages"]])
            self.assertNotIn(private_id, [message["id"] for message in payload["messages"]])
        users = self.request("/api/users", token=alice["token"])[1]["users"]
        self.assertIn(bob["user"], users)
        self.assertTrue(all(set(user) == {"id", "username"} for user in users))

    def test_history_cursor_returns_earliest_followups_and_latest_initial(self):
        alice, bob = self.register(), self.register()
        ids = []
        for index in range(5):
            status, payload, _ = self.request("/api/messages", method="POST", token=alice["token"],
                data={"body": "Message " + str(index), "recipient_id": bob["user"]["id"]})
            self.assertEqual(status, 201)
            ids.append(payload["message"]["id"])
        path = "/api/messages?peer_id=" + str(bob["user"]["id"])
        payload = self.request(path + "&limit=2", token=alice["token"])[1]
        self.assertEqual([message["id"] for message in payload["messages"]], ids[-2:])
        self.assertFalse(payload["has_more"])
        payload = self.request(path + "&limit=2&after_id=0", token=alice["token"])[1]
        self.assertEqual([message["id"] for message in payload["messages"]], ids[:2])
        self.assertTrue(payload["has_more"])
        payload = self.request(path + "&limit=2&after_id=" + str(ids[3]), token=alice["token"])[1]
        self.assertEqual([message["id"] for message in payload["messages"]], ids[4:])
        self.assertFalse(payload["has_more"])

    def test_strict_validation_and_message_sender_cannot_be_forged(self):
        for credentials in (
            {"username": "ab", "password": PASSWORD},
            {"username": "bad username", "password": PASSWORD},
            {"username": "测试用户", "password": PASSWORD},
            {"username": "okay_name", "password": "short"},
            {"username": "okay_name", "password": "x" * 129},
            {"username": "okay_name", "password": PASSWORD, "admin": True},
            {"username": 1234, "password": PASSWORD},
        ):
            status, payload, _ = self.request("/api/auth/register", method="POST", data=credentials)
            self.assertEqual(status, 422, credentials)
            self.assertNotIn(PASSWORD, json.dumps(payload))
        account = self.register()
        for message in ({"body": ""}, {"body": " "}, {"body": "x" * 4001}, {"body": "bad\x00text"},
                        {"body": 42}, {"body": "hello", "recipient_id": True},
                        {"body": "hello", "recipient_id": "1"}, {"body": "hello", "sender_id": 42}):
            self.assertEqual(self.request("/api/messages", method="POST", token=account["token"], data=message)[0], 422, message)
        self.assertEqual(self.request("/api/messages", method="POST", token=account["token"],
                                     data={"body": "hello", "recipient_id": 9223372036854775807})[0], 404)
        for query in ("limit=0", "limit=101", "after_id=-1", "peer_id=-1", "peer_id=999999999999999999999"):
            self.assertEqual(self.request("/api/messages?" + query, token=account["token"])[0], 422)

    def test_request_cap_and_same_origin_browser_write_policy(self):
        status, payload, _ = self.request("/api/auth/register", method="POST", raw=b"x" * 16385)
        self.assertEqual(status, 413)
        self.assertIn("16 KiB", payload["detail"])
        credentials = {"username": "origin_user", "password": PASSWORD}
        self.assertEqual(self.request("/api/auth/register", method="POST", data=credentials,
                                      headers={"Origin": "https://another-site.example"})[0], 403)
        self.assertEqual(self.request("/api/auth/register", method="POST", data=credentials,
                                      headers={"Origin": self.address})[0], 201)

    def test_reset_password_revokes_all_sessions_and_persists(self):
        account = self.register()
        changed = "a new strong passphrase"
        store = ChatStore(self.database, clock=lambda: self.now[0])
        store.reset_password(account["user"]["username"].upper(), changed)
        self.assertEqual(self.request("/api/me", token=account["token"])[0], 401)
        old = self.request("/api/auth/login", method="POST", data={"username": account["user"]["username"], "password": PASSWORD})
        self.assertEqual(old[0], 401)
        restored = ChatStore(self.database, clock=lambda: self.now[0])
        session = restored.login(account["user"]["username"], changed)
        self.assertEqual(restored.authenticate(session["token"]), account["user"])
        message = restored.send(account["user"]["id"], "Survives a restart")
        self.assertIn(message, ChatStore(self.database, clock=lambda: self.now[0]).messages(account["user"]["id"])["messages"])

    def test_api_rate_limits_return_retry_after_without_hashing(self):
        original_ip = self.app.state.auth_limiter
        original_account = self.app.state.account_limiter
        original_send = self.app.state.send_limiter
        try:
            self.app.state.auth_limiter.maximum = 1
            self.app.state.auth_limiter.attempts.clear()
            request = {"username": "missing_limited", "password": PASSWORD}
            self.assertEqual(self.request("/api/auth/login", method="POST", data=request)[0], 401)
            with patch.object(self.app.state.store, "_verify", side_effect=AssertionError("must not hash")):
                status, _, headers = self.request("/api/auth/login", method="POST", data=request)
            self.assertEqual(status, 429)
            self.assertEqual(headers["Retry-After"], "60")
            self.app.state.auth_limiter.maximum = 1000
            self.app.state.auth_limiter.attempts.clear()
            self.app.state.account_limiter.maximum = 1
            self.app.state.account_limiter.attempts.clear()
            self.assertEqual(self.request("/api/auth/login", method="POST", data=request)[0], 401)
            request["username"] = request["username"].upper()
            with patch.object(self.app.state.store, "_verify", side_effect=AssertionError("must not hash")):
                self.assertEqual(self.request("/api/auth/login", method="POST", data=request)[0], 429)
            self.app.state.account_limiter.maximum = 1000
            self.app.state.account_limiter.attempts.clear()
            account = self.register()
            self.app.state.send_limiter.maximum = 1
            self.app.state.send_limiter.attempts.clear()
            self.assertEqual(self.request("/api/messages", method="POST", token=account["token"], data={"body": "One"})[0], 201)
            self.assertEqual(self.request("/api/messages", method="POST", token=account["token"], data={"body": "Two"})[0], 429)
        finally:
            original_ip.maximum = 1000
            original_account.maximum = 1000
            original_send.maximum = 1000
            original_ip.attempts.clear()
            original_account.attempts.clear()
            original_send.attempts.clear()


@unittest.skipUnless(SERVER_AVAILABLE, "Optional backend dependencies: install requirements-server.txt")
class WorkServerGuardTests(unittest.TestCase):
    def guarded_request(self, *, client="127.0.0.1", scheme="http", headers=None, chunks=None,
                        allow_lan_http=False):
        called, sent = [], []
        events = list(chunks or [{"type": "http.request", "body": b"", "more_body": False}])

        async def downstream(scope, receive, send):
            called.append(await receive())
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"OK"})

        async def receive():
            return events.pop(0) if events else {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "method": "POST", "scheme": scheme,
                 "client": (client, 1234), "headers": headers or []}
        asyncio.run(RequestGuard(downstream, allow_lan_http=allow_lan_http)(scope, receive, send))
        return called, sent

    def test_socket_peer_allowlist_ignores_forwarded_headers_and_demands_tls(self):
        for value in ("10.0.0.3", "172.16.1.2", "192.168.1.2", "169.254.3.2", "::1", "fc12::3", "fe80::1", "::ffff:192.168.1.2"):
            self.assertTrue(is_lan_address(value), value)
        for value in ("8.8.8.8", "172.15.0.1", "172.32.0.1", "100.64.0.1", "192.0.0.1", "2001:4860:4860::8888", "not-an-ip"):
            self.assertFalse(is_lan_address(value), value)
        called, sent = self.guarded_request(client="8.8.8.8", headers=[(b"x-forwarded-for", b"127.0.0.1")])
        self.assertFalse(called)
        self.assertEqual(sent[0]["status"], 403)
        called, sent = self.guarded_request(client="192.168.1.5")
        self.assertFalse(called)
        self.assertEqual(sent[0]["status"], 426)
        called, sent = self.guarded_request(client="192.168.1.5", scheme="https")
        self.assertTrue(called)
        self.assertEqual(sent[0]["status"], 200)

    def test_opted_in_lan_http_still_rejects_public_socket_peers(self):
        for address in ("192.168.50.32", "10.2.3.4", "fc12::3", "::ffff:192.168.50.32"):
            called, sent = self.guarded_request(client=address, allow_lan_http=True)
            self.assertTrue(called, address)
            self.assertEqual(sent[0]["status"], 200)
            self.assertNotIn(b"strict-transport-security", dict(sent[0]["headers"]))
        for address in ("8.8.8.8", "2001:4860:4860::8888", "not-an-ip"):
            called, sent = self.guarded_request(client=address, allow_lan_http=True,
                headers=[(b"x-forwarded-for", b"192.168.50.32")])
            self.assertFalse(called, address)
            self.assertEqual(sent[0]["status"], 403)

    def test_create_app_lan_http_option_preserves_session_and_origin_checks(self):
        async def request(app, path, *, client="192.168.50.32", method="GET", headers=None):
            sent = []

            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(message):
                sent.append(message)

            scope = {"type": "http", "http_version": "1.1", "method": method,
                     "scheme": "http", "path": path, "raw_path": path.encode(),
                     "root_path": "", "query_string": b"", "client": (client, 1234),
                     "server": ("192.168.50.194", 8765), "headers": headers or []}
            await app(scope, receive, send)
            return sent[0]["status"]

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "chat.sqlite"
            default_app = create_app(database)
            app = create_app(database, allow_lan_http=True)
            self.assertEqual(asyncio.run(request(default_app, "/api/health")), 426)
            self.assertEqual(asyncio.run(request(app, "/api/health")), 200)
            self.assertEqual(asyncio.run(request(app, "/api/me")), 401)
            self.assertEqual(asyncio.run(request(app, "/api/health", client="8.8.8.8")), 403)
            self.assertEqual(asyncio.run(request(app, "/api/auth/register", method="POST",
                headers=[(b"host", b"192.168.50.194:8765"),
                         (b"origin", b"http://another-site.example")])), 403)

    def test_streamed_request_cap_before_downstream_and_validation(self):
        called, sent = self.guarded_request(chunks=[
            {"type": "http.request", "body": b"a" * 16000, "more_body": True},
            {"type": "http.request", "body": b"b" * 385, "more_body": False},
        ])
        self.assertFalse(called)
        self.assertEqual(sent[0]["status"], 413)
        called, sent = self.guarded_request(chunks=[
            {"type": "http.request", "body": b"a" * 16000, "more_body": True},
            {"type": "http.request", "body": b"b" * 384, "more_body": False},
        ])
        self.assertEqual(len(called[0]["body"]), 16384)
        self.assertEqual(sent[0]["status"], 200)

    def test_rate_limiter_window_bounded_keys_and_case_normalized_account(self):
        now = [10.0]
        limiter = RateLimiter(2, 60, clock=lambda: now[0], key_limit=1)
        limiter.check("first")
        limiter.check("first")
        with self.assertRaises(StoreError):
            limiter.check("first")
        with self.assertRaises(StoreError):
            limiter.check("second")
        now[0] += 61
        limiter.check("second")
        self.assertEqual(len(limiter.attempts), 1)

    def test_invalid_password_precedes_hash_and_hash_work_has_capacity_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ChatStore(Path(directory) / "chat.sqlite", hash_slots=1)
            with patch.object(store, "_hash", side_effect=AssertionError("must not hash")):
                for password in ("short", "x" * 129):
                    with self.assertRaises(StoreError) as error:
                        store.register("staff_user", password)
                    self.assertEqual(error.exception.status, 422)
            store.hash_slots.acquire()
            try:
                with self.assertRaises(StoreError) as error:
                    store.register("staff_user", PASSWORD)
                self.assertEqual(error.exception.status, 429)
            finally:
                store.hash_slots.release()

    def test_partial_request_deadline_prevents_downstream_work(self):
        called, sent = [], []

        async def downstream(scope, receive, send):
            called.append(True)

        async def stalled_receive():
            await asyncio.sleep(1)
            return {"type": "http.request", "body": b"", "more_body": True}

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "method": "POST", "scheme": "http", "client": ("127.0.0.1", 1234), "headers": []}
        asyncio.run(RequestGuard(downstream, body_timeout_seconds=0.01)(scope, stalled_receive, send))
        self.assertFalse(called)
        self.assertEqual(sent[0]["status"], 408)

    def test_account_capacity_bounds_directory_and_refuses_more_signups(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ChatStore(Path(directory) / "chat.sqlite", max_users=1)
            store.register("staff_one", PASSWORD)
            self.assertEqual(len(store.users()), 1)
            with patch.object(store, "_hash", side_effect=AssertionError("must not hash")):
                with self.assertRaises(StoreError) as error:
                    store.register("staff_two", PASSWORD)
            self.assertEqual(error.exception.status, 503)

    def test_cli_rejects_lan_http_before_opening_database(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "uncreated" / "chat.sqlite"
            result = subprocess.run([sys.executable, "-m", "work_server", "--host", "0.0.0.0",
                                     "--database", str(database)],
                                    cwd=project, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("LAN connections require HTTPS", result.stderr)
            self.assertFalse(database.parent.exists())

    def test_cli_allows_explicit_lan_http_and_keeps_proxy_headers_disabled(self):
        from work_server.__main__ import main

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "chat.sqlite"
            with patch("work_server.app.create_app", wraps=create_app) as factory, patch("uvicorn.run") as run:
                main(["--host", "0.0.0.0", "--port", "8765", "--allow-lan-http",
                      "--database", str(database)])
            factory.assert_called_once_with(str(database), allow_lan_http=True)
            self.assertTrue(database.is_file())
            self.assertEqual(run.call_args.kwargs["host"], "0.0.0.0")
            self.assertEqual(run.call_args.kwargs["port"], 8765)
            self.assertIsNone(run.call_args.kwargs["ssl_certfile"])
            self.assertIsNone(run.call_args.kwargs["ssl_keyfile"])
            self.assertFalse(run.call_args.kwargs["proxy_headers"])

    def test_cli_tls_still_works_and_invalid_options_have_no_database_side_effects(self):
        from work_server.__main__ import main

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "chat.sqlite"
            certificate, key = Path(directory) / "server.crt", Path(directory) / "server.key"
            certificate.write_text("certificate", encoding="utf-8")
            key.write_text("private key", encoding="utf-8")
            base = ["--host", "0.0.0.0", "--database", str(database)]
            for options in (["--allow-lan-http", "--certfile", str(certificate)],
                            ["--allow-lan-http", "--port", "0"],
                            ["--allow-lan-http", "--certfile", str(certificate),
                             "--keyfile", str(Path(directory) / "missing.key")]):
                with self.subTest(options=options), patch("sys.stderr"), self.assertRaises(SystemExit) as error:
                    main(base + options)
                self.assertEqual(error.exception.code, 2)
                self.assertFalse(database.exists())
            with patch("work_server.app.create_app", wraps=create_app) as factory, patch("uvicorn.run") as run:
                main(base + ["--certfile", str(certificate), "--keyfile", str(key)])
            factory.assert_called_once_with(str(database), allow_lan_http=False)
            self.assertEqual(run.call_args.kwargs["ssl_certfile"], str(certificate))
            self.assertEqual(run.call_args.kwargs["ssl_keyfile"], str(key))


if __name__ == "__main__":
    unittest.main()
