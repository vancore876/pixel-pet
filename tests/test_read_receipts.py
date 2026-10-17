"""Receipt privacy, monotonic cursors, revocation, and additive API contracts."""
from contextlib import closing
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

AVAILABLE = all(importlib.util.find_spec(name) for name in ("fastapi", "httpx", "argon2"))
if AVAILABLE:
    from fastapi.testclient import TestClient
    from work_server.app import create_app
    from work_server.store import ChatStore, StoreError

PASSWORD = "receipt testing password"


@unittest.skipUnless(AVAILABLE, "Install requirements-server-dev.txt")
class ReadReceiptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.app = create_app(Path(cls.directory.name) / "chat.sqlite", auth_limit=10000, send_limit=10000)
        cls.client = TestClient(cls.app, client=("127.0.0.1", 51000))
        cls.store = cls.app.state.store
        cls.counter = 0

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.directory.cleanup()

    def setUp(self):
        type(self).counter += 1
        self.accounts = [self.store.register(f"receipt_{self.counter}_{name}", PASSWORD)
                         for name in ("alice", "bob", "cara")]
        self.alice, self.bob, self.cara = [account["user"]["id"] for account in self.accounts]
        self.app.state.receipt_limiter.attempts.clear()

    def request(self, method="GET", account=0, body=None, query=""):
        headers = {} if account is None else {"Authorization": "Bearer " + self.accounts[account]["token"]}
        return self.client.request(method, "/api/read-receipts" + query, headers=headers, json=body)

    def send(self, sender, recipient=None):
        return self.store.send(sender, "Receipt test", recipient)["id"]

    def test_direct_receipt_only_the_author_and_correct_reader(self):
        sent = self.send(self.alice, self.bob)
        response = self.request("POST", 1, {"peer_id": self.alice, "through_id": sent})
        self.assertEqual(response.status_code, 200, response.text)
        response = self.request(query=f"?peer_id={self.bob}")
        self.assertEqual(response.json()["receipts"], [{"message_id": sent, "read_count": 1,
                         "readers": [self.accounts[1]["user"]]}])
        self.assertEqual(self.request(account=2, query=f"?peer_id={self.bob}").json()["receipts"], [])
        self.assertEqual(self.request(account=1, query=f"?peer_id={self.alice}").json()["receipts"], [])

    def test_cross_conversation_own_and_missing_message_cannot_mark_read(self):
        personal = self.send(self.alice, self.bob)
        own = self.send(self.bob)
        team = self.send(self.alice)
        for account, peer, message in ((2, self.alice, personal), (1, None, personal),
                (1, self.cara, personal), (1, None, own), (1, self.alice, team), (1, None, 2**60)):
            with self.subTest(account=account, peer=peer, message=message):
                response = self.request("POST", account, {"peer_id": peer, "through_id": message})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["detail"], "Choose an incoming message from this conversation.")

    def test_monotonic_receipt_survives_reopen_and_older_pc_cursor(self):
        first = self.send(self.alice, self.bob)
        second = self.send(self.alice, self.bob)
        for through in (second, first, second):
            result = self.request("POST", 1, {"peer_id": self.alice, "through_id": through})
            self.assertEqual(result.json()["through_id"], second)
        reopened = ChatStore(self.store.path)
        receipts = reopened.message_receipts(self.alice, peer_id=self.bob)["receipts"]
        self.assertEqual([r["message_id"] for r in receipts], [first, second])
        self.assertEqual([r["read_count"] for r in receipts], [1, 1])

    def test_team_receipts_exclude_self_deleted_and_other_personal_conversations(self):
        message = self.send(self.alice)
        personal = self.send(self.alice, self.cara)
        self.store.mark_read(self.bob, through_id=message)
        self.store.mark_read(self.cara, peer_id=self.alice, through_id=personal)
        receipt = self.request().json()["receipts"][-1]
        self.assertEqual(receipt["readers"], [self.accounts[1]["user"]])
        self.store.delete_account(self.bob, PASSWORD, self.accounts[1]["user"]["username"])
        receipt = self.request().json()["receipts"][-1]
        self.assertEqual((receipt["read_count"], receipt["readers"]), (0, []))

    def test_team_reader_names_bounded_and_count_complete(self):
        message = self.send(self.alice)
        # Populate additional reader identities without slow, irrelevant password hashing.
        with closing(self.store._connect()) as connection, connection:
            for index in range(25):
                cursor = connection.execute("INSERT INTO users(username,password_hash,created_at) VALUES (?, 'unused', 'now')",
                                            (f"bounded_{self.counter}_{index}",))
                connection.execute("INSERT INTO read_receipts VALUES (?,0,?)", (cursor.lastrowid, message))
        receipt = self.request().json()["receipts"][-1]
        self.assertEqual(receipt["read_count"], 25)
        self.assertEqual(len(receipt["readers"]), 20)

    def test_latest_sent_receipts_and_cursor_are_bounded(self):
        ids = [self.send(self.alice, self.bob) for _ in range(55)]
        receipts = self.request(query=f"?peer_id={self.bob}").json()["receipts"]
        self.assertEqual([r["message_id"] for r in receipts], ids[-50:])
        receipts = self.request(query=f"?peer_id={self.bob}&after_id={ids[-4]}&limit=2").json()["receipts"]
        self.assertEqual([r["message_id"] for r in receipts], ids[-2:])

    def test_authentication_required_and_revoked_session_rejected(self):
        message = self.send(self.alice)
        self.assertEqual(self.request(account=None).status_code, 401)
        self.assertEqual(self.request("POST", None, {"through_id": message}).status_code, 401)
        self.store.logout(self.accounts[1]["token"])
        self.assertEqual(self.request("POST", 1, {"through_id": message}).status_code, 401)
        self.assertEqual(self.request(account=1).status_code, 401)

    def test_receipt_rechecks_revocation_between_dependency_and_transaction(self):
        message = self.send(self.alice)
        original = self.store.mark_read
        def revoke_then_mark(*args, **kwargs):
            self.store.logout(self.accounts[1]["token"])
            return original(*args, **kwargs)
        with patch.object(self.store, "mark_read", side_effect=revoke_then_mark):
            self.assertEqual(self.request("POST", 1, {"through_id": message}).status_code, 401)
        self.assertEqual(self.store.message_receipts(self.alice)["receipts"][-1]["read_count"], 0)
        original_get = self.store.message_receipts
        def revoke_then_get(*args, **kwargs):
            self.store.logout(self.accounts[0]["token"])
            return original_get(*args, **kwargs)
        with patch.object(self.store, "message_receipts", side_effect=revoke_then_get):
            self.assertEqual(self.request().status_code, 401)

    def test_payload_and_query_bounds_and_rate_limit(self):
        message = self.send(self.alice)
        for body in ({"through_id": True}, {"through_id": "1"}, {"through_id": 0},
                     {"through_id": message, "peer_id": False}, {"through_id": message, "extra": 1},
                     {"through_id": 2**63}, {"through_id": message, "peer_id": 2**63}):
            self.assertEqual(self.request("POST", 1, body).status_code, 422)
        for query in ("?limit=51", "?limit=0", "?after_id=-1", "?peer_id=0", "?after_id=" + str(2**63)):
            self.assertEqual(self.request(query=query).status_code, 422)
        self.app.state.receipt_limiter.maximum = 1
        try:
            self.assertEqual(self.request().status_code, 200)
            response = self.request()
            self.assertEqual(response.status_code, 429)
            self.assertIn("Retry-After", response.headers)
        finally:
            self.app.state.receipt_limiter.maximum = 120

    def test_deleted_actor_rejected_by_store_and_peer_int_bounds(self):
        message = self.send(self.alice)
        for peer in (True, 0, -1, 2**63, "2"):
            with self.assertRaises(StoreError) as caught:
                self.store.mark_read(self.bob, peer_id=peer, through_id=message)
            self.assertEqual(caught.exception.status, 422)
        self.store.delete_account(self.bob, PASSWORD, self.accounts[1]["user"]["username"])
        with self.assertRaises(StoreError) as caught:
            self.store.mark_read(self.bob, through_id=message)
        self.assertEqual(caught.exception.status, 401)


if __name__ == "__main__":
    unittest.main()
