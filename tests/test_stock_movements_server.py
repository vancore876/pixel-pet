"""Authenticated shared scanner requests, retry receipts, and employee audit history."""
from contextlib import closing
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
AVAILABLE = all(importlib.util.find_spec(name) for name in ("fastapi", "httpx", "argon2"))
if AVAILABLE:
    from fastapi.testclient import TestClient
    from work_server.app import create_app


@unittest.skipUnless(AVAILABLE, "Install requirements-server-dev.txt")
class StockMovementServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.app = create_app(Path(cls.directory.name) / "office.sqlite", auth_limit=1000, account_auth_limit=1000)
        cls.client = TestClient(cls.app, client=("127.0.0.1", 50000))
        cls.client.__enter__()
        cls.logins = []
        for name in ("counter_a", "counter_b"):
            cls.logins.append(cls.client.post("/api/auth/register", json={"username": name,
                              "password": "correct horse battery staple"}).json())
        cls.headers = [{"Authorization": "Bearer " + login["token"]} for login in cls.logins]

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.directory.cleanup()

    def setUp(self):
        self.sku = "P-" + uuid.uuid4().hex[:12]
        self.barcode = "000" + uuid.uuid4().hex[:16]
        self.app.state.inventory.import_rows([{"sku": self.sku, "quantity": 10, "barcode": self.barcode}])

    def payload(self, **updates):
        return {"sku": self.sku, "kind": "sale", "quantity": 2, "expected_revision":
                self.app.state.inventory.get(self.sku)["revision"], "request_id": uuid.uuid4().hex, **updates}

    def post(self, payload=None, headers=None):
        return self.client.post("/api/inventory/movements", json=payload or self.payload(),
                                headers=self.headers[0] if headers is None else headers)

    def test_scans_and_history_require_authentication(self):
        for path in ("/api/inventory/scan?code=" + self.barcode, "/api/inventory/movements"):
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.post(headers={}).status_code, 401)
        found = self.client.get("/api/inventory/scan", params={"code": self.barcode}, headers=self.headers[1])
        self.assertEqual(found.json()["item"]["sku"], self.sku)
        self.assertEqual(self.app.state.inventory.get(self.sku)["quantity"], 10)

    def test_movement_shared_to_other_pc_and_actor_is_server_derived(self):
        payload = self.payload()
        response = self.post(payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["movement"]["actor"], "counter_a")
        self.assertEqual(response.json()["item"]["quantity"], 8)
        history = self.client.get("/api/inventory/movements", params={"sku": self.sku}, headers=self.headers[1]).json()
        self.assertEqual(history["movements"][0]["delta"], -2)
        self.assertEqual(history["movements"][0]["actor"], "counter_a")
        self.assertNotIn("actor_id", history["movements"][0])
        self.assertEqual(self.post(self.payload(actor="pretend_admin")).status_code, 422)
        self.assertEqual(self.app.state.inventory.get(self.sku)["quantity"], 8)

    def test_server_retry_exactly_once_and_conflicting_id_rejected(self):
        payload = self.payload()
        original = self.post(payload)
        self.assertEqual(self.post(payload).json(), original.json())
        self.assertEqual(self.post(payload | {"quantity": 3}).status_code, 409)
        self.assertEqual(self.app.state.inventory.get(self.sku)["quantity"], 8)
        # A different employee has a separate request namespace, but still must
        # supply the current item revision; copying an old request cannot win.
        self.assertEqual(self.post(payload, self.headers[1]).status_code, 409)

    def test_bad_request_unknown_code_and_negative_stock_are_safe(self):
        self.assertEqual(self.client.get("/api/inventory/scan", params={"code": "absent-" + self.sku},
                         headers=self.headers[0]).status_code, 404)
        for updates, status in (({"quantity": True}, 422), ({"quantity": "2"}, 422),
                                ({"quantity": 11}, 409), ({"kind": "count", "reason": ""}, 422),
                                ({"request_id": "../bad"}, 422), ({"sku": "absent"}, 404)):
            with self.subTest(updates=updates):
                self.assertEqual(self.post(self.payload(**updates)).status_code, status)
        self.assertEqual(self.app.state.inventory.get(self.sku)["quantity"], 10)
        for query in ("limit=201", "before_id=0", "before_id=9223372036854775808"):
            self.assertEqual(self.client.get("/api/inventory/movements?" + query, headers=self.headers[0]).status_code, 422)

    def test_simultaneous_clients_conflict_without_lost_update(self):
        payload = self.payload()
        self.assertEqual(self.post(payload).status_code, 200)
        stale = self.post(payload | {"request_id": uuid.uuid4().hex}, self.headers[1])
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.app.state.inventory.get(self.sku)["quantity"], 8)

    def test_session_revoked_after_dependency_auth_cannot_commit(self):
        login = self.app.state.store.login("counter_a", "correct horse battery staple")
        auth = {"Authorization": "Bearer " + login["token"]}
        original_authenticate = self.app.state.store.authenticate
        def authenticate_then_revoke(token):
            user = original_authenticate(token)
            self.app.state.store.logout(token)
            return user
        with patch.object(self.app.state.store, "authenticate", side_effect=authenticate_then_revoke):
            response = self.post(headers=auth)
        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(self.app.state.inventory.get(self.sku)["quantity"], 10)

    def test_staged_imports_record_employee_and_reject_duplicate_barcode_across_chunks(self):
        revision = self.app.state.inventory.summary()["revision"]
        stage = self.client.post("/api/inventory/imports", headers=self.headers[0],
                                json={"expected_revision": revision, "source": "stock.csv"}).json()["id"]
        rows = [{"sku": self.sku, "quantity": 9, "barcode": self.barcode},
                {"sku": "COPY-" + self.sku, "quantity": 4, "barcode": self.barcode}]
        for index, row in enumerate(rows):
            response = self.client.post(f"/api/inventory/imports/{stage}/rows", headers=self.headers[0],
                                        json={"rows": [row], "offset": index})
            self.assertEqual(response.status_code, 200)
        response = self.client.post(f"/api/inventory/imports/{stage}/commit", headers=self.headers[0], json={})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.app.state.inventory.get(self.sku)["quantity"], 10)
        self.assertIsNone(self.app.state.inventory.get("COPY-" + self.sku))
        stage = self.client.post("/api/inventory/imports", headers=self.headers[0],
                                json={"expected_revision": revision, "source": "stock.csv"}).json()["id"]
        self.client.post(f"/api/inventory/imports/{stage}/rows", headers=self.headers[0], json={"rows": [rows[0]]})
        committed = self.client.post(f"/api/inventory/imports/{stage}/commit", headers=self.headers[0], json={})
        self.assertEqual(committed.status_code, 200, committed.text)
        movement = self.app.state.inventory.movements(self.sku)["movements"][0]
        self.assertEqual(movement["actor"], "counter_a")
        self.assertEqual(movement["kind"], "import")
        self.assertEqual(movement["delta"], -1)
        self.assertEqual(self.client.post(f"/api/inventory/imports/{stage}/commit", headers=self.headers[0], json={}).json(), committed.json())
        self.assertEqual(len(self.app.state.inventory.movements(self.sku)["movements"]), 2)

    def test_deleted_employee_history_anonymized_and_tokens_cannot_scan(self):
        login = self.app.state.store.register("departed_" + uuid.uuid4().hex[:8], "correct horse battery staple")
        auth = {"Authorization": "Bearer " + login["token"]}
        user = self.app.state.store.authenticate(login["token"])
        self.assertEqual(self.post(headers=auth).status_code, 200)
        self.app.state.store.delete_account(user["id"], "correct horse battery staple", user["username"])
        self.assertEqual(self.client.get("/api/inventory/scan", params={"code": self.barcode}, headers=auth).status_code, 401)
        history = self.app.state.inventory.movements(self.sku)["movements"]
        self.assertEqual(history[0]["actor"], "Deleted employee")
        self.assertEqual(history[0]["delta"], -2)
        with closing(self.app.state.inventory._connect()) as connection:
            self.assertEqual(connection.execute("SELECT actor FROM inventory_movements WHERE id=?", (history[0]["id"],)).fetchone()[0], "")
            result = connection.execute("SELECT result_json FROM inventory_movement_requests WHERE actor_key=?", ("user:" + str(user["id"]),)).fetchone()[0]
        self.assertNotIn(user["username"], result)


if __name__ == "__main__":
    unittest.main()
