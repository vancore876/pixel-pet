"""Real SQLite snapshots, stopped-server recovery, and isolated IT scheduling."""
from __future__ import annotations

from contextlib import closing
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
from threading import Event, Thread
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
AVAILABLE = all(importlib.util.find_spec(module) for module in ("fastapi", "httpx", "argon2"))
if AVAILABLE:
    from fastapi.testclient import TestClient
    from work_server.app import create_app
    from work_server.backups import (BackupError, BackupManager, DatabaseLease, create_backup,
                                     list_backups, prune_backups, restore_backup, verify_backup)
    from work_server.store import StoreError

PASSWORD = "private office IT passphrase"
EMPLOYEE_PASSWORD = "private employee passphrase"


@unittest.skipUnless(AVAILABLE, "Install requirements-server-dev.txt")
class OfficeBackupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.database = self.root / "office.sqlite"
        self.snapshots = self.root / "office-backups"
        self.now = [1_800_000_000.0]
        self.app = create_app(self.database, clock=lambda: self.now[0])
        self.store, self.admin = self.app.state.store, self.app.state.it_admin
        self.manager = self.app.state.backups
        self.addCleanup(lambda: self.manager.stop(timeout=5))

    def ready(self):
        first = self.admin.login("admin", "admin123", local=True)
        return self.admin.change_password(first["token"], "admin123", PASSWORD, local=True)

    def snapshot(self, *, now=None):
        value = create_backup(self.database, self.snapshots,
                              clock=lambda: self.now[0] if now is None else now)
        return self.snapshots / value["name"]

    def rewrite_manifest(self, path):
        sidecar = path.with_name(path.name + ".json")
        value = json.loads(sidecar.read_text())
        value.update(size=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        sidecar.write_text(json.dumps(value))

    def wait_until(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.01)
        self.fail("Background backup did not finish in time")

    @staticmethod
    def headers(session):
        return {"Authorization": "Bearer " + session["token"]}

    def test_snapshot_includes_committed_wal_and_is_standalone_and_private(self):
        with closing(sqlite3.connect(self.database)) as writer:
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("UPDATE inventory_state SET revision=42 WHERE id=1")
            writer.commit()
            self.assertTrue(Path(str(self.database) + "-wal").exists())
            path = self.snapshot()
        manifest = verify_backup(path)
        with closing(sqlite3.connect(path)) as snapshot:
            self.assertEqual(snapshot.execute("SELECT revision FROM inventory_state").fetchone()[0], 42)
            self.assertEqual(snapshot.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        self.assertEqual(manifest["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertFalse(Path(str(path) + "-wal").exists())
        self.assertEqual(len(list_backups(self.snapshots)), 1)
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.snapshots.stat().st_mode & 0o777, 0o700)

    def test_failed_publication_leaves_no_partial_or_unmanifested_snapshot(self):
        with patch("work_server.backups._hash_file", side_effect=OSError("private disk detail")):
            with self.assertRaises(OSError):
                self.snapshot()
        self.assertEqual(list(self.snapshots.iterdir()), [])

        original_replace = os.replace

        def fail_manifest(source, destination):
            if str(destination).endswith(".json"):
                raise OSError("cannot publish manifest")
            return original_replace(source, destination)

        with patch("work_server.backups.os.replace", side_effect=fail_manifest):
            with self.assertRaises(OSError):
                self.snapshot()
        self.assertEqual(list(self.snapshots.iterdir()), [])

    def test_snapshot_and_restore_fsync_use_windows_compatible_writable_handles(self):
        original_fsync = os.fsync
        checked = []

        def writable_fsync(descriptor):
            if stat.S_ISREG(os.fstat(descriptor).st_mode):
                # A zero-byte write changes no data but fails on read-only
                # descriptors, mirroring Windows FlushFileBuffers' requirement.
                os.write(descriptor, b"")
                checked.append(descriptor)
            return original_fsync(descriptor)

        with patch("work_server.backups.os.fsync", side_effect=writable_fsync):
            path = self.snapshot()
            restore_backup(path, self.database, confirm=True)
        self.assertGreaterEqual(len(checked), 6)

    def test_checksum_manifest_and_truncated_files_are_rejected(self):
        path = self.snapshot()
        original = path.read_bytes()
        changed = bytearray(original)
        changed[-1] ^= 1
        path.write_bytes(changed)
        with self.assertRaisesRegex(BackupError, "checksum"):
            verify_backup(path)
        path.write_bytes(original[:1024])
        with self.assertRaisesRegex(BackupError, "manifest"):
            verify_backup(path)
        path.write_bytes(original)
        sidecar = path.with_name(path.name + ".json")
        sidecar.write_text("{}")
        with self.assertRaisesRegex(BackupError, "manifest"):
            verify_backup(path)
        sidecar.write_text("x" * 4097)
        with self.assertRaisesRegex(BackupError, "manifest"):
            verify_backup(path)

    def test_schema_and_foreign_keys_checked_even_when_checksum_matches(self):
        path = self.snapshot()
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("DROP TABLE recovery_codes")
        self.rewrite_manifest(path)
        with self.assertRaisesRegex(BackupError, "complete"):
            verify_backup(path)
        path = self.snapshot()
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("INSERT INTO sessions VALUES('dangling',123456,9999999999)")
        self.rewrite_manifest(path)
        with self.assertRaisesRegex(BackupError, "references"):
            verify_backup(path)

    def test_nonstandalone_snapshot_and_mismatched_timestamp_are_rejected(self):
        path = self.snapshot()
        wal = path.with_name(path.name + "-wal")
        wal.write_bytes(b"untrusted sidecar")
        with self.assertRaisesRegex(BackupError, "standalone"):
            verify_backup(path)
        wal.unlink()
        sidecar = path.with_name(path.name + ".json")
        value = json.loads(sidecar.read_text())
        value["created_at"] = "2099-01-01T00:00:00.000Z"
        sidecar.write_text(json.dumps(value))
        with self.assertRaisesRegex(BackupError, "timestamp"):
            verify_backup(path)

    def test_cancelled_backup_does_not_publish(self):
        stop = Event()
        stop.set()
        with self.assertRaises(BackupError):
            create_backup(self.database, self.snapshots, stop_event=stop)
        self.assertEqual(list(self.snapshots.iterdir()), [])

    def test_symlink_paths_and_manifest_links_are_rejected(self):
        linked = self.root / "linked.sqlite"
        try:
            linked.symlink_to(self.database)
        except OSError as error:
            self.skipTest("Symlink creation is unavailable: " + str(error))
        with self.assertRaisesRegex(BackupError, "symbolic"):
            create_backup(linked, self.snapshots)
        path = self.snapshot()
        manifest = path.with_name(path.name + ".json")
        moved = self.root / "moved-manifest.json"
        manifest.replace(moved)
        manifest.symlink_to(moved)
        with self.assertRaisesRegex(BackupError, "symbolic"):
            verify_backup(path)

    def test_retention_keeps_latest_per_date_and_preserves_unknown_or_corrupt_files(self):
        first = self.snapshot(now=self.now[0])
        second = self.snapshot(now=self.now[0] + 60)
        third = self.snapshot(now=self.now[0] + 86400)
        fourth = self.snapshot(now=self.now[0] + 2 * 86400)
        unknown = self.snapshots / "unrelated.sqlite"
        unknown.write_text("retain this")
        # A same-size corruption keeps valid-looking metadata but must never be
        # pruned based on its name alone.
        altered = bytearray(first.read_bytes())
        altered[-1] ^= 1
        first.write_bytes(altered)
        prune_backups(self.snapshots, 2)
        self.assertTrue(first.exists())
        self.assertFalse(second.exists())
        self.assertTrue(third.exists())
        self.assertTrue(fourth.exists())
        self.assertTrue(unknown.exists())

    def test_corrupt_newer_snapshot_cannot_displace_older_recoverable_snapshots(self):
        older_day = self.snapshot(now=self.now[0])
        older_same_day = self.snapshot(now=self.now[0] + 86400)
        latest = self.snapshot(now=self.now[0] + 86460)
        changed = bytearray(latest.read_bytes())
        changed[-1] ^= 1
        latest.write_bytes(changed)
        prune_backups(self.snapshots, 2)
        self.assertTrue(older_day.exists())
        self.assertTrue(older_same_day.exists())
        self.assertTrue(latest.exists())
        verify_backup(older_day)
        verify_backup(older_same_day)

    def test_real_restore_preserves_passwords_and_stock_revokes_sessions_and_saves_old_data(self):
        admin = self.ready()
        employee = self.store.register("counter", EMPLOYEE_PASSWORD)
        self.store.send(employee["user"]["id"], "Restored customer information")
        self.app.state.inventory.import_rows([{"sku": "00071", "quantity": 12, "location": "A3", "barcode": "0012345"}])
        path = self.snapshot()
        self.store.send(employee["user"]["id"], "Created after backup")
        self.app.state.inventory.import_rows([{"sku": "00071", "quantity": 1, "location": "B4", "barcode": "0012345"}])
        result = restore_backup(path, self.database, confirm=True)
        self.assertTrue(result["sessions_revoked"])
        safety = Path(result["safety_backup"])
        verify_backup(safety)
        with closing(sqlite3.connect(safety)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT quantity FROM inventory_items").fetchone()[0], 1)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT quantity,location,barcode FROM inventory_items").fetchone(), (12, "A3", "0012345"))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM it_admin_sessions").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT action FROM it_admin_audit ORDER BY id DESC LIMIT 1").fetchone()[0], "backup_restored")
        with self.assertRaises(StoreError):
            self.store.authenticate(employee["token"])
        with self.assertRaises(StoreError):
            self.admin.overview(admin["token"])
        self.assertTrue(self.store.login("counter", EMPLOYEE_PASSWORD)["token"])
        self.assertFalse(self.admin.login("admin", PASSWORD)["must_change_password"])
        with self.assertRaises(StoreError):
            self.admin.login("admin", "admin123")

    def test_restore_requires_confirmation_existing_destination_and_distinct_backup(self):
        path = self.snapshot()
        before = self.database.read_bytes()
        for target, confirm in ((self.database, False), (self.root / "typo.sqlite", True), (path, True)):
            with self.assertRaises(BackupError):
                restore_backup(path, target, confirm=confirm)
        self.assertEqual(self.database.read_bytes(), before)
        self.assertFalse((self.root / "typo.sqlite").exists())
        self.assertFalse((self.root / "restore-safety-backups").exists())

    def test_running_server_lease_prevents_restore_and_second_process(self):
        path = self.snapshot()
        with DatabaseLease(self.database) as lease:
            lease.acquire()  # The owner may acquire idempotently.
            with self.assertRaisesRegex(BackupError, "in use"):
                restore_backup(path, self.database, confirm=True)
            command = [sys.executable, "-c", "from work_server.backups import DatabaseLease; import sys; DatabaseLease(sys.argv[1]).acquire()", str(self.database)]
            process = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10)
            self.assertNotEqual(process.returncode, 0)
            self.assertIn("in use", process.stderr)
        with DatabaseLease(self.database):
            pass
        self.assertTrue(lease.path.exists())

    def test_restore_corruption_and_replace_failure_leave_original_data(self):
        path = self.snapshot()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("UPDATE inventory_state SET revision=99")
        original_replace = os.replace

        def fail_restore(source, destination):
            if Path(destination) == self.database:
                raise OSError("replace failed")
            return original_replace(source, destination)

        with patch("work_server.backups.os.replace", side_effect=fail_restore), self.assertRaises(OSError):
            restore_backup(path, self.database, confirm=True)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT revision FROM inventory_state").fetchone()[0], 99)
        self.assertEqual(len(list_backups(self.root / "restore-safety-backups")), 1)
        self.assertFalse(list(self.root.glob(".*.restore-*")))
        changed = bytearray(path.read_bytes())
        changed[-1] ^= 1
        path.write_bytes(changed)
        with self.assertRaises(BackupError):
            restore_backup(path, self.database, confirm=True)

    def test_restore_detects_source_changed_after_verification(self):
        path = self.snapshot()
        original = verify_backup

        def alter_after_verifying(candidate):
            value = original(candidate)
            data = bytearray(Path(candidate).read_bytes())
            data[-1] ^= 1
            Path(candidate).write_bytes(data)
            return value

        with patch("work_server.backups.verify_backup", side_effect=alter_after_verifying):
            with self.assertRaisesRegex(BackupError, "changed during recovery"):
                restore_backup(path, self.database, confirm=True)
        self.assertFalse(list(self.root.glob(".*.restore-*")))

    def test_default_factory_has_no_worker_and_lifespan_holds_lease(self):
        self.assertFalse(self.manager.enabled)
        self.assertIsNone(self.manager._thread)
        with TestClient(self.app, client=("127.0.0.1", 5000)):
            self.assertIsNone(self.manager._thread)
            with self.assertRaises(BackupError):
                DatabaseLease(self.database).acquire()
        with DatabaseLease(self.database):
            pass

    def test_automatic_daily_run_is_idempotent_and_catches_up_after_restart(self):
        self.manager.enabled = True
        self.manager.start()
        self.wait_until(lambda: self.manager.status()["last_success_at"] is not None and not self.manager.status()["running"])
        thread = self.manager._thread
        self.manager.start()
        self.assertIs(self.manager._thread, thread)
        self.assertEqual(len(list_backups(self.snapshots)), 1)
        self.assertTrue(self.manager.stop())
        restarted = BackupManager(self.store, self.admin, enabled=True)
        self.addCleanup(restarted.stop)
        restarted.start()
        self.assertEqual(len(list_backups(self.snapshots)), 1)
        self.now[0] += 86400
        restarted._wake.set()
        self.wait_until(lambda: len(list_backups(self.snapshots)) == 2 and not restarted.status()["running"])
        self.assertIsNone(restarted.status()["last_error"])

    def test_only_one_backup_job_can_run_and_failures_have_safe_status(self):
        entered, release = Event(), Event()

        def failed_backup(*args, **kwargs):
            entered.set()
            release.wait(5)
            raise OSError("secret password and private path")

        with patch("work_server.backups.create_backup", side_effect=failed_backup):
            self.assertTrue(self.manager.request(lambda: None))
            self.assertTrue(entered.wait(2))
            self.assertFalse(self.manager.request(lambda: self.fail("duplicate must not authorize")))
            release.set()
            self.wait_until(lambda: not self.manager.status()["running"])
        status = self.manager.status()
        self.assertIn("Backup failed", status["last_error"])
        self.assertNotIn("secret", json.dumps(status))
        self.assertEqual(status["backups"], [])
        self.assertIsNone(status["next_due"])

    def test_it_api_authentication_bootstrap_and_strict_inputs(self):
        bootstrap = self.admin.login("admin", "admin123", local=True)
        employee = self.store.register("counter", EMPLOYEE_PASSWORD)
        client = TestClient(self.app, client=("127.0.0.1", 5000))
        for session, expected in ((None, 401), (employee, 401), (bootstrap, 403)):
            headers = self.headers(session) if session else {}
            self.assertEqual(client.get("/api/it/backups", headers=headers).status_code, expected)
            self.assertEqual(client.post("/api/it/backups", headers=headers, json={"admin_password": PASSWORD}).status_code, expected)
        admin = self.ready()
        headers = self.headers(admin)
        status = client.get("/api/it/backups", headers=headers)
        self.assertEqual(status.status_code, 200)
        self.assertEqual(set(status.json()), {"enabled", "running", "last_success_at", "last_error", "next_due", "backups", "directory", "keep_days"})
        for body in ({"admin_password": PASSWORD, "directory": "/tmp/arbitrary"},
                     {"admin_password": 123456789012}, {"admin_password": "short"}):
            response = client.post("/api/it/backups", headers=headers, json=body)
            self.assertEqual(response.status_code, 422)
            self.assertNotIn(PASSWORD, response.text)
        self.assertEqual(client.post("/api/it/backups", headers=headers, json={"admin_password": "wrong private password"}).status_code, 401)

    def test_manual_api_returns_accepted_runs_async_and_audits_completion(self):
        admin = self.ready()
        client = TestClient(self.app, client=("127.0.0.1", 5000))
        entered, release = Event(), Event()
        original = create_backup

        def blocked_backup(*args, **kwargs):
            entered.set()
            release.wait(5)
            return original(*args, **kwargs)

        with patch("work_server.backups.create_backup", side_effect=blocked_backup):
            response = client.post("/api/it/backups", headers=self.headers(admin), json={"admin_password": PASSWORD})
            self.assertEqual(response.status_code, 202, response.text)
            self.assertTrue(entered.wait(2))
            self.assertTrue(client.get("/api/it/backups", headers=self.headers(admin)).json()["running"])
            self.assertEqual(client.post("/api/it/backups", headers=self.headers(admin), json={"admin_password": PASSWORD}).status_code, 409)
            release.set()
            self.wait_until(lambda: not self.manager.status()["running"])
        status = client.get("/api/it/backups", headers=self.headers(admin)).json()
        self.assertEqual(len(status["backups"]), 1)
        self.assertTrue(status["backups"][0]["verified"])
        actions = [item["action"] for item in self.admin.overview(admin["token"])["audit"]]
        self.assertEqual(actions.count("backup_requested"), 1)
        self.assertIn("backup_completed", actions)

    def test_revocation_during_password_proof_prevents_scheduling(self):
        admin = self.ready()
        client = TestClient(self.app, client=("127.0.0.1", 5000))
        original = self.admin._prove_password

        def revoke(*args, **kwargs):
            proof = original(*args, **kwargs)
            self.admin.logout(admin["token"])
            return proof

        with patch.object(self.admin, "_prove_password", side_effect=revoke):
            response = client.post("/api/it/backups", headers=self.headers(admin), json={"admin_password": PASSWORD})
        self.assertEqual(response.status_code, 401)
        self.assertIsNone(self.manager._thread)
        self.assertFalse(self.manager.status()["running"])
        self.assertFalse(self.snapshots.exists())

    def test_shutdown_waits_for_worker_before_releasing_database(self):
        entered, release = Event(), Event()

        def wait_for_release(*args, **kwargs):
            entered.set()
            release.wait(5)
            raise BackupError("cancelled")

        client = TestClient(self.app, client=("127.0.0.1", 5000))
        with patch("work_server.backups.create_backup", side_effect=wait_for_release):
            client.__enter__()
            self.manager.request(lambda: None)
            self.assertTrue(entered.wait(2))
            closing_thread = Thread(target=lambda: client.__exit__(None, None, None))
            closing_thread.start()
            self.wait_until(self.manager._stop.is_set)
            with self.assertRaises(BackupError):
                DatabaseLease(self.database).acquire()
            release.set()
            closing_thread.join(5)
            self.assertFalse(closing_thread.is_alive())
        with DatabaseLease(self.database):
            pass

    def test_bounded_shutdown_defers_release_for_a_stalled_worker(self):
        entered, release = Event(), Event()
        self.addCleanup(release.set)

        def blocked(*args, **kwargs):
            entered.set()
            release.wait(5)
            raise BackupError("cancelled")

        original_stop = self.manager.stop
        with patch("work_server.backups.create_backup", side_effect=blocked), \
                patch.object(self.manager, "stop", side_effect=lambda: original_stop(timeout=.01)):
            with TestClient(self.app, client=("127.0.0.1", 5000)):
                self.manager.request(lambda: None)
                self.assertTrue(entered.wait(2))
            self.assertTrue(self.manager._thread.is_alive())
            self.assertTrue(self.app.state.database_lease.acquired)
            with self.assertRaises(BackupError):
                DatabaseLease(self.database).acquire()
            release.set()
            self.wait_until(lambda: not self.app.state.database_lease.acquired)
        with DatabaseLease(self.database):
            pass

    def test_startup_failure_releases_lifespan_and_cli_leases(self):
        self.manager.enabled = True
        with patch.object(self.manager, "start", side_effect=BackupError("start failed")):
            with self.assertRaises(BackupError):
                with TestClient(self.app, client=("127.0.0.1", 5000)):
                    self.fail("startup should fail")
        with DatabaseLease(self.database):
            pass
        from work_server.__main__ import main
        with patch("work_server.app.create_app", side_effect=BackupError("construction failed")), patch("sys.stderr"):
            with self.assertRaises(SystemExit):
                main(["--database", str(self.database)])
        with DatabaseLease(self.database):
            pass

    def test_cli_failure_does_not_release_a_stalled_workers_lease(self):
        from work_server.__main__ import main
        entered, release = Event(), Event()
        self.addCleanup(release.set)
        launched = []

        def blocked(*args, **kwargs):
            entered.set()
            release.wait(5)
            raise BackupError("cancelled")

        def failed_server(application, **kwargs):
            launched.append(application)
            application.state.backups.request(lambda: None)
            self.assertTrue(entered.wait(2))
            raise RuntimeError("unexpected launcher exit")

        original_stop = BackupManager.stop
        with patch("work_server.backups.create_backup", side_effect=blocked), \
                patch.object(BackupManager, "stop", lambda manager, **unused: original_stop(manager, timeout=.01)), \
                patch("uvicorn.run", side_effect=failed_server):
            with self.assertRaises(RuntimeError):
                main(["--database", str(self.database)])
            with self.assertRaises(BackupError):
                DatabaseLease(self.database).acquire()
            release.set()
            self.wait_until(lambda: not launched[0].state.database_lease.acquired)
        with DatabaseLease(self.database):
            pass

    def test_cli_verify_restore_defaults_and_preconstruction_lock(self):
        from work_server.__main__ import main
        path = self.snapshot()
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            main(["verify-backup", str(path)])
        self.assertIn("Backup verified", output.getvalue())
        with DatabaseLease(self.database), patch("work_server.app.create_app") as factory, patch("sys.stderr"):
            with self.assertRaises(SystemExit):
                main(["--database", str(self.database)])
            factory.assert_not_called()
        with patch("uvicorn.run") as run:
            main(["--database", str(self.database), "--no-auto-backup", "--backup-keep-days", "7",
                  "--backup-directory", str(self.root / "private-backups")])
        application = run.call_args.args[0]
        self.assertFalse(application.state.backups.enabled)
        self.assertEqual(application.state.backups.keep_days, 7)
        self.assertEqual(application.state.backups.directory, self.root / "private-backups")
        self.assertFalse(application.state.database_lease.acquired)
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            main(["restore-backup", str(path), "--database", str(self.database), "--confirm-restore"])
        self.assertIn("restored", output.getvalue())
        self.assertIn("sessions have been signed out", output.getvalue())

    def test_configuration_rejects_invalid_retention_before_cli_database_creation(self):
        from work_server.__main__ import main
        missing = self.root / "missing" / "wrong.sqlite"
        for value in ("0", "366", "no"):
            with patch("sys.stderr"), self.assertRaises(SystemExit):
                main(["--database", str(missing), "--backup-keep-days", value])
            self.assertFalse(missing.parent.exists())
        for value in (0, 366, True, "14"):
            with self.assertRaises(BackupError):
                BackupManager(self.store, self.admin, keep_days=value)


if __name__ == "__main__":
    unittest.main()
