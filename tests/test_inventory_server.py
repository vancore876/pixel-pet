"""Authenticated office stock, staged writes, isolation, and retry safety."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
AVAILABLE = all(importlib.util.find_spec(name) for name in ("fastapi", "httpx", "argon2"))
if AVAILABLE:
    from fastapi.testclient import TestClient
    from work_server.app import create_app


@unittest.skipUnless(AVAILABLE, "Install the optional work server dependencies")
class InventoryServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.now = [1_800_000_000.0]
        cls.app = create_app(Path(cls.directory.name) / "work.sqlite", clock=lambda: cls.now[0],
                             auth_limit=1000, account_auth_limit=1000)
        cls.client = TestClient(cls.app, client=("127.0.0.1", 50000))
        cls.client.__enter__()
        cls.headers = []
        for name in ("stock_a", "stock_b"):
            response = cls.client.post("/api/auth/register", json={"username": name, "password": "correct horse battery staple"})
            cls.headers.append({"Authorization": "Bearer " + response.json()["token"]})

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.directory.cleanup()

    def revision(self):
        return self.client.get("/api/inventory/summary", headers=self.headers[0]).json()["revision"]

    def stage(self, source="parts.xlsx"):
        response = self.client.post("/api/inventory/imports", headers=self.headers[0],
                                    json={"source": source, "expected_revision": self.revision()})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def upload(self, stage, rows, **extra):
        return self.client.post(f"/api/inventory/imports/{stage}/rows", headers=self.headers[0],
                                json={"rows": rows, **extra})

    def commit(self, stage):
        return self.client.post(f"/api/inventory/imports/{stage}/commit", headers=self.headers[0], json={})

    def test_all_inventory_routes_require_signin(self):
        for path in ("/api/inventory", "/api/inventory/summary", "/api/inventory/items/A"):
            self.assertEqual(self.client.get(path).status_code, 401)
        for path, body in (("/api/inventory/imports", {"expected_revision": 0}),
                           ("/api/inventory/imports/" + "a" * 32 + "/rows", {"rows": [{"sku": "A", "quantity": 1}]}),
                           ("/api/inventory/imports/" + "a" * 32 + "/commit", {})):
            self.assertEqual(self.client.post(path, json=body).status_code, 401)

    def test_staged_stock_invisible_until_commit_then_shared_and_retry_safe(self):
        stage = self.stage(r"C:\private\parts.xlsx")
        old_revision = self.revision()
        rows = [{"sku": "00071", "quantity": 2, "description": "Air filter", "location": "Bin 7", "reorder_point": 3}]
        self.assertEqual(self.upload(stage, rows, offset=0).status_code, 200)
        self.assertEqual(self.upload(stage, rows, offset=0).json()["rows"], 1)
        self.assertEqual(self.revision(), old_revision)
        self.assertEqual(self.client.get("/api/inventory/items/00071", headers=self.headers[1]).status_code, 404)
        response = self.commit(stage)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["revision"], old_revision + 1)
        self.assertEqual(response.json()["last_import_source"], "parts.xlsx")
        self.assertEqual(response.json()["imported_by"], "stock_a")
        replay = self.commit(stage)
        self.assertEqual(replay.json(), response.json())
        self.assertEqual(self.revision(), old_revision + 1)
        item = self.client.get("/api/inventory/items/00071", headers=self.headers[1]).json()
        self.assertEqual(item["location"], "Bin 7")
        self.assertTrue(item["low_stock"])

    def test_stages_belong_to_signed_in_creator(self):
        stage = self.stage()
        for suffix, method, body in (("/rows", "POST", {"rows": [{"sku": "SECRET", "quantity": 1}]}),
                                     ("/commit", "POST", {}), ("", "DELETE", None)):
            response = self.client.request(method, "/api/inventory/imports/" + stage + suffix,
                                           headers=self.headers[1], json=body)
            self.assertEqual(response.status_code, 404)
        self.client.delete("/api/inventory/imports/" + stage, headers=self.headers[0])

    def test_duplicate_across_chunks_rejected_without_partial_batch(self):
        stage = self.stage()
        self.assertEqual(self.upload(stage, [{"sku": "DUP", "quantity": 1}]).status_code, 200)
        response = self.upload(stage, [{"sku": "new", "quantity": 9}, {"sku": "dup", "quantity": 2}])
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.commit(stage).json()["rows"], 1)
        self.assertEqual(self.client.get("/api/inventory/items/new", headers=self.headers[0]).status_code, 404)

    def test_concurrent_inventory_edit_invalidates_stale_import(self):
        first, second = self.stage(), self.stage()
        self.upload(first, [{"sku": "RACE", "quantity": 3}])
        self.upload(second, [{"sku": "RACE", "quantity": 9}])
        self.assertEqual(self.commit(first).status_code, 200)
        response = self.commit(second)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.client.get("/api/inventory/items/RACE", headers=self.headers[0]).json()["quantity"], 3)
        self.client.delete("/api/inventory/imports/" + second, headers=self.headers[0])

    def test_long_description_batches_use_larger_guard_and_limit_is_bounded(self):
        stage = self.stage()
        rows = [{"sku": "LONG" + str(index), "quantity": 1, "description": "a" * 2000} for index in range(20)]
        self.assertEqual(self.upload(stage, rows).status_code, 200)
        oversized = [{"sku": "BIG" + str(index), "quantity": 1, "description": "b" * 2000} for index in range(140)]
        self.assertEqual(self.upload(stage, oversized).status_code, 413)
        self.assertEqual(self.commit(stage).json()["rows"], 20)

    def test_expired_stage_cannot_change_stock(self):
        stage = self.stage()
        self.upload(stage, [{"sku": "EXPIRED", "quantity": 1}])
        self.now[0] += 601
        self.assertEqual(self.commit(stage).status_code, 404)
        self.assertEqual(self.client.get("/api/inventory/items/EXPIRED", headers=self.headers[0]).status_code, 404)

    def test_invalid_stock_and_out_of_order_chunks_are_rejected(self):
        stage = self.stage()
        self.assertEqual(self.upload(stage, [{"sku": "BAD", "quantity": 0.5}]).status_code, 422)
        self.assertEqual(self.upload(stage, [{"sku": "VALID", "quantity": 2}], offset=2).status_code, 409)
        self.assertEqual(self.commit(stage).status_code, 422)
        self.client.delete("/api/inventory/imports/" + stage, headers=self.headers[0])


if __name__ == "__main__":
    unittest.main()
