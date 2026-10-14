"""Share operations use temporary folders and never send real notifications."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

import shared_workspace
from shared_workspace import (SharedWorkspace, SharedWorkspaceCancelled,
                              SharedWorkspaceError)


class SharedWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.share = self.root / "selected"
        self.share.mkdir()
        self.workspace = SharedWorkspace(self.share)

    def tearDown(self):
        self.directory.cleanup()

    def event(self, **extra):
        return {"event_id": uuid.uuid4().hex, "type": "note", "payload": "Tea 🐉", **extra}

    def text_file(self, text="Café 🐉\n" * 20000):
        source = self.root / "text.txt"
        source.write_text(text, encoding="utf-8")
        return source

    def test_workspace_identity_is_stable_and_existing_selection_required(self):
        self.assertEqual(SharedWorkspace(self.share).workspace_id, self.workspace.workspace_id)
        self.assertRegex(self.workspace.workspace_id, r"^[0-9a-f]{32}$")
        with self.assertRaises(SharedWorkspaceError):
            SharedWorkspace(self.root / "not-an-existing-folder")
        self.assertFalse((self.root / "not-an-existing-folder").exists())

    def test_concurrent_workspace_creation_never_replaces_identity(self):
        another = self.root / "other"
        another.mkdir()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            identifiers = list(pool.map(lambda _: SharedWorkspace(another).workspace_id, range(24)))
        self.assertEqual(len(set(identifiers)), 1)

    def test_corrupt_and_replaced_manifest_are_rejected(self):
        self.workspace.manifest_path.write_text("{}", encoding="utf-8")
        with self.assertRaises(SharedWorkspaceError):
            SharedWorkspace(self.share)
        with self.assertRaises(SharedWorkspaceError):
            self.workspace.read_events()
        self.workspace.manifest_path.write_text(json.dumps({"version": 1,
                                                           "workspace_id": uuid.uuid4().hex}))
        with self.assertRaises(SharedWorkspaceError):
            self.workspace.publish_event(self.event())

    def test_event_publish_is_exact_idempotent_and_conflicts_fail(self):
        event = self.event()
        self.assertTrue(self.workspace.publish_event(event))
        self.assertFalse(self.workspace.publish_event(dict(reversed(list(event.items())))))
        with self.assertRaises(SharedWorkspaceError):
            self.workspace.publish_event({**event, "payload": "Changed"})
        events, warnings = self.workspace.read_events()
        self.assertEqual(events, [{**event, "workspace_id": self.workspace.workspace_id}])
        self.assertEqual(warnings, [])

    def test_concurrent_identical_event_publish_has_one_winner(self):
        event = self.event()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.workspace.publish_event(event), range(24)))
        self.assertEqual(sum(results), 1)

    def test_invalid_event_ids_size_and_cross_workspace_are_rejected(self):
        for identifier in ("../escape", "A" * 32, "a" * 31, True, "a" * 32 + ".json"):
            with self.assertRaises(SharedWorkspaceError):
                self.workspace.publish_event(self.event(event_id=identifier))
        with self.assertRaises(SharedWorkspaceError):
            self.workspace.publish_event(self.event(workspace_id=uuid.uuid4().hex))
        with self.assertRaises(SharedWorkspaceError):
            self.workspace.publish_event(self.event(payload="x" * shared_workspace.MAX_EVENT_BYTES))
        with self.assertRaises(SharedWorkspaceError):
            self.workspace.publish_event(self.event(payload=float("nan")))
        self.assertEqual(self.workspace.read_events(), ([], []))

    def test_invalid_files_are_skipped_and_events_are_deterministic(self):
        for number in (3, 1, 2):
            self.workspace.publish_event(self.event(event_id=f"{number:032x}"))
        event_path = self.workspace.events_path
        (event_path / ".interrupted.tmp").write_text("partial")
        (event_path / "bad.json").write_text("{}")
        (event_path / ("4" * 32 + ".json")).write_text("not-json")
        (event_path / ("5" * 32 + ".json")).write_text(json.dumps(self.event()))
        (event_path / ("6" * 32 + ".json")).write_bytes(b"x" * (shared_workspace.MAX_EVENT_BYTES + 1))
        duplicate = '{"event_id":"' + "7" * 32 + '","event_id":"' + "7" * 32 + '"}'
        (event_path / ("7" * 32 + ".json")).write_text(duplicate)
        events, warnings = self.workspace.read_events()
        self.assertEqual([event["event_id"] for event in events], [f"{n:032x}" for n in (1, 2, 3)])
        self.assertEqual(len(warnings), 5)

    def test_utf8_blob_round_trip_counts_characters_and_replays(self):
        text = "é🐉z\n" * 180000
        source = self.text_file(text)
        with patch.object(shared_workspace, "COPY_BYTES", 7):
            result = self.workspace.upload_blob(source, len(text))
            self.assertEqual(result["sha256"], hashlib.sha256(text.encode()).hexdigest())
            self.assertEqual(result["characters"], len(text))
            self.assertEqual(result["bytes"], len(text.encode()))
            self.assertEqual(self.workspace.upload_blob(source, len(text)), result)
            destination = self.root / "download.txt"
            self.workspace.download_blob(result["sha256"], destination, len(text))
        self.assertEqual(destination.read_text(encoding="utf-8"), text)
        self.assertEqual(len(list(self.workspace.blobs_path.glob("*.txt"))), 1)
        self.assertEqual(list(self.workspace.blobs_path.glob("*.tmp")), [])

    def test_wrong_count_invalid_utf8_and_hash_preserve_destination(self):
        source = self.text_file("Good text 🐉")
        with self.assertRaises(SharedWorkspaceError):
            self.workspace.upload_blob(source, 1)
        source.write_bytes(b"invalid\xff")
        with self.assertRaises(SharedWorkspaceError):
            self.workspace.upload_blob(source)
        source.write_text("Good text 🐉", encoding="utf-8")
        result = self.workspace.upload_blob(source)
        destination = self.root / "existing.txt"
        destination.write_text("Keep this")
        with self.assertRaises(SharedWorkspaceError):
            self.workspace.download_blob(result["sha256"], destination, 1)
        self.assertEqual(destination.read_text(), "Keep this")
        (self.workspace.blobs_path / (result["sha256"] + ".txt")).write_text("Tampered")
        with self.assertRaises(SharedWorkspaceError):
            self.workspace.download_blob(result["sha256"], destination, result["characters"])
        with self.assertRaises(SharedWorkspaceError):
            self.workspace.upload_blob(source)
        self.assertEqual(destination.read_text(), "Keep this")
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_cancelled_copies_do_not_publish_or_replace_files(self):
        source = self.text_file("Long 🐉 text" * 100)
        calls = 0

        def cancel():
            nonlocal calls
            calls += 1
            return calls >= 5

        with patch.object(shared_workspace, "COPY_BYTES", 16):
            with self.assertRaises(SharedWorkspaceCancelled):
                self.workspace.upload_blob(source, cancel=cancel)
        self.assertEqual(list(self.workspace.blobs_path.iterdir()), [])
        result = self.workspace.upload_blob(source)
        destination = self.root / "existing.txt"
        destination.write_text("Keep")
        calls = 0
        with patch.object(shared_workspace, "COPY_BYTES", 16):
            with self.assertRaises(SharedWorkspaceCancelled):
                self.workspace.download_blob(result["sha256"], destination,
                                             result["characters"], cancel=cancel)
        self.assertEqual(destination.read_text(), "Keep")
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(SharedWorkspaceCancelled):
            self.workspace.download_blob(result["sha256"], destination,
                                         result["characters"], cancel=cancelled)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_blob_bounds_and_path_hashes_are_checked(self):
        source = self.text_file("Four")
        with patch.object(shared_workspace, "MAX_DOCUMENT_CHARACTERS", 3):
            with self.assertRaises(SharedWorkspaceError):
                self.workspace.upload_blob(source)
        for count in (-1, True, 1_000_000_001):
            with self.assertRaises(SharedWorkspaceError):
                self.workspace.upload_blob(source, count)
        for digest in ("../file", "F" * 64, "f" * 63, True):
            with self.assertRaises(SharedWorkspaceError):
                self.workspace.download_blob(digest, self.root / "target", 4)

    def test_sqlite_files_and_download_into_workspace_are_rejected(self):
        source = self.text_file("SQLite format 3\0database contents")
        with self.assertRaises(SharedWorkspaceError):
            self.workspace.upload_blob(source)
        source.write_text("Actual document")
        result = self.workspace.upload_blob(source)
        with self.assertRaises(SharedWorkspaceError):
            self.workspace.download_blob(result["sha256"], self.workspace.path / "notes.txt",
                                         result["characters"])
        self.assertFalse((self.workspace.path / "notes.txt").exists())

    def test_interrupted_disk_writes_preserve_destination_and_report_transport_error(self):
        source = self.text_file("Document text")
        result = self.workspace.upload_blob(source)
        destination = self.root / "existing.txt"
        destination.write_text("Keep")
        with patch.object(shared_workspace.os, "fsync", side_effect=OSError("Disconnected")):
            with self.assertRaises(SharedWorkspaceError):
                self.workspace.download_blob(result["sha256"], destination, result["characters"])
            with self.assertRaises(SharedWorkspaceError):
                self.workspace.publish_event(self.event())
        self.assertEqual(destination.read_text(), "Keep")
        self.assertEqual(self.workspace.read_events(), ([], []))
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_notification_lease_completion_and_employee_isolation(self):
        token = self.workspace.claim_notification("alice", "note", "due", now=100)
        self.assertIsNotNone(token)
        second = SharedWorkspace(self.share)
        self.assertIsNone(second.claim_notification("alice", "note", "due", now=101))
        self.assertIsNotNone(second.claim_notification("bob", "note", "due", now=101))
        self.assertFalse(second.complete_notification("alice", "note", "due", "a" * 32, now=102))
        self.assertTrue(self.workspace.complete_notification("alice", "note", "due", token, now=102))
        self.assertIsNone(second.claim_notification("alice", "note", "due", now=99999))
        self.assertTrue(second.complete_notification("alice", "note", "due", token, now=99999))
        self.assertIsNotNone(second.claim_notification("alice", "note", "next-due", now=103))

    def test_expired_claims_retry_and_old_tokens_cannot_complete_or_release(self):
        old = self.workspace.claim_notification("alice", "note", "due", now=100)
        new = self.workspace.claim_notification("alice", "note", "due", now=131)
        self.assertNotEqual(old, new)
        self.assertFalse(self.workspace.complete_notification("alice", "note", "due", old, now=132))
        self.assertFalse(self.workspace.release_notification("alice", "note", "due", old))
        self.assertTrue(self.workspace.release_notification("alice", "note", "due", new))
        self.assertIsNotNone(self.workspace.claim_notification("alice", "note", "due", now=132))

    def test_unavailable_share_never_claims_or_recreates_it(self):
        displaced = self.root / "disconnected"
        self.share.rename(displaced)
        with self.assertRaises(SharedWorkspaceError):
            self.workspace.claim_notification("alice", "note", "due")
        self.assertFalse(self.share.exists())

    def test_manifest_events_and_blob_symlinks_are_rejected(self):
        external = self.root / "external.txt"
        external.write_text("Private")
        identifier = "a" * 32
        path = self.workspace.events_path / (identifier + ".json")
        try:
            path.symlink_to(external)
        except (OSError, NotImplementedError):
            self.skipTest("Symlink creation unavailable")
        self.assertEqual(len(self.workspace.read_events()[1]), 1)
        with self.assertRaises(SharedWorkspaceError):
            self.workspace.publish_event(self.event(event_id=identifier))
        digest = hashlib.sha256(b"Private").hexdigest()
        (self.workspace.blobs_path / (digest + ".txt")).symlink_to(external)
        with self.assertRaises(SharedWorkspaceError):
            self.workspace.download_blob(digest, self.root / "target", 7)
        self.assertEqual(external.read_text(), "Private")


if __name__ == "__main__":
    unittest.main()
