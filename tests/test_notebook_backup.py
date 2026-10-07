import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import warnings
import zipfile

import documents
from notebook_backup import (BackupCancelled, export_backup, export_text,
                             notebook_snapshot, prepare_backup)
from notes import NoteStore


class NotebookBackupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.store = NoteStore(self.root / "source" / "notes.json")
        self.targets = []

    def tearDown(self):
        self.store.close()
        for store in self.targets:
            store.close()
        self.directory.cleanup()

    def document(self, text, title="Document", store=None):
        store = self.store if store is None else store
        prepared = store.documents.prepare_text(text, title)
        return store.add_prepared_document(prepared, title, "document.pdf", now=100)

    def archive(self, payload, bodies, name="input.zip", extras=()):
        path = self.root / name
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("notebook.json", json.dumps(payload, ensure_ascii=False))
            for identifier, body in bodies.items():
                archive.writestr(f"documents/{identifier}.txt", body)
            for entry, body in extras:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    archive.writestr(entry, body)
        return path

    def target(self):
        store = NoteStore(self.root / "target" / "notes.json")
        self.targets.append(store)
        return store

    def test_zip_round_trip_keeps_full_unicode_and_metadata_with_new_ids(self):
        text = ("Café 🐉 medieval notebook\n" * 10000) + "The last page remembers an appointment."
        original = self.document(text)
        normal = self.store.add("Daily list", "Tea tomorrow", repeat=0, now=101,
                                details={"checklist": [{"id": "tea", "text": "Tea", "quantity": 2, "done": True}]})
        self.store.complete(normal["id"])
        path = self.root / "backup.zip"
        self.assertEqual(export_backup(self.store, path)["kind"], "backup_exported")
        with zipfile.ZipFile(path) as archive:
            metadata = json.loads(archive.read("notebook.json"))
            self.assertLessEqual(len(metadata["notes"][0]["body"]), 10000)
            self.assertEqual(archive.read(f"documents/{original['document_id']}.txt").decode("utf-8"), text)
        target = self.target()
        result = prepare_backup({"store": target, "path": path})
        self.assertEqual(target.notes, [])
        self.assertEqual(len(result["prepared_ids"]), 1)
        restored = result["payload"]["notes"][0]
        self.assertNotEqual(restored["document_id"], original["document_id"])
        self.assertEqual(restored["body_characters"], len(text))
        self.assertEqual(restored["body_sha256"], hashlib.sha256(text.encode()).hexdigest())
        self.assertEqual("".join(target.documents.iter_text(restored["document_id"])), text)
        self.assertTrue(result["payload"]["notes"][1]["done"])
        self.assertTrue(result["payload"]["notes"][1]["checklist"][0]["done"])
        target.import_backup(result["payload"], allow_documents=True)
        reloaded = NoteStore(target.path)
        self.assertEqual(reloaded.character_count(), len(text) + len("Tea tomorrow"))

    def test_text_export_uses_full_document_and_completion_and_checklist(self):
        text = "Start\n" + "a" * 12001 + "\nLast page"
        self.document(text)
        note = self.store.add("Order", "Pick up", repeat=0, now=105, details={
            "kind": "order", "customer": "Maria", "order_ref": "Z9",
            "checklist": [{"id": "item", "text": "Bread", "quantity": 3, "done": True}],
        })
        self.store.complete(note["id"])
        path = self.root / "notebook.txt"
        export_text({"store": self.store, "path": path})
        exported = path.read_text(encoding="utf-8")
        self.assertIn(text, exported)
        self.assertIn("[Done] Order", exported)
        self.assertIn("Customer: Maria", exported)
        self.assertIn("Order reference: Z9", exported)
        self.assertIn("[x] 3 x Bread", exported)

    def test_gui_snapshot_makes_export_independent_of_later_note_edits(self):
        note = self.store.add("Original", "Original body", repeat=0, now=100)
        snapshot = notebook_snapshot(self.store)
        self.store.edit(note["id"], "Changed", "Changed body", 0, now=200)
        path = self.root / "snapshot.txt"
        export_text({"store": self.store, "path": path, "snapshot": snapshot})
        exported = path.read_text()
        self.assertIn("Original body", exported)
        self.assertNotIn("Changed body", exported)

    def test_corrupt_body_is_rejected_before_any_document_is_prepared(self):
        note = self.document("trusted text")
        target = self.target()
        target.add("Existing", "Keep me", 0, now=100)
        previous = copy.deepcopy(target.state)
        path = self.archive(self.store.state, {note["document_id"]: "altered text"})
        with patch.object(target.documents, "prepare_import", wraps=target.documents.prepare_import) as prepare:
            with self.assertRaisesRegex(ValueError, "integrity|character"):
                prepare_backup({"store": target, "path": path})
            prepare.assert_not_called()
        self.assertEqual(target.state, previous)
        self.assertEqual(target.documents.total_characters(), 0)

    def test_unexpected_paths_duplicate_entries_and_missing_documents_are_rejected(self):
        note = self.document("saved text")
        for name, bodies, extras in (
            ("path.zip", {note["document_id"]: "saved text"}, [("../escape.txt", "no")]),
            ("extra.zip", {note["document_id"]: "saved text", "a" * 32: "extra"}, []),
            ("missing.zip", {}, []),
            ("duplicate.zip", {note["document_id"]: "saved text"}, [("notebook.json", "{}")]),
        ):
            with self.subTest(name=name):
                path = self.archive(self.store.state, bodies, name, extras)
                target = self.target()
                with self.assertRaises(ValueError):
                    prepare_backup({"store": target, "path": path})
                self.assertEqual(target.notes, [])
                self.assertEqual(target.documents.total_characters(), 0)
                self.assertFalse((self.root / "escape.txt").exists())

    def test_plain_json_import_accepts_legacy_notes_and_rejects_missing_document_text(self):
        self.store.add("Original", "Plain note", 0, now=100)
        path = self.root / "legacy.json"
        path.write_text(json.dumps(self.store.state), encoding="utf-8")
        target = self.target()
        result = prepare_backup({"store": target, "path": path})
        self.assertEqual(result["prepared_ids"], [])
        target.import_backup(result["payload"])
        self.assertEqual(target.notes[0]["body"], "Plain note")
        self.document("large imported document")
        path.write_text(json.dumps(self.store.state), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "ZIP"):
            prepare_backup({"store": target, "path": path})

    def test_invalid_utf8_count_and_oversized_metadata_are_rejected_without_writes(self):
        note = self.document("saved text")
        target = self.target()
        bad_utf8 = self.archive(self.store.state, {note["document_id"]: b"\xffsaved text"}, "utf8.zip")
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            prepare_backup({"store": target, "path": bad_utf8})
        payload = copy.deepcopy(self.store.state)
        payload["notes"][0]["body_characters"] += 1
        bad_count = self.archive(payload, {note["document_id"]: "saved text"}, "count.zip")
        with self.assertRaisesRegex(ValueError, "integrity"):
            prepare_backup({"store": target, "path": bad_count})
        valid = self.archive(self.store.state, {note["document_id"]: "saved text"}, "valid.zip")
        with patch("notebook_backup.MAX_METADATA_BYTES", 16):
            with self.assertRaisesRegex(ValueError, "metadata"):
                prepare_backup({"store": target, "path": valid})
        self.assertEqual(target.notes, [])
        self.assertEqual(target.documents.total_characters(), 0)

    def test_one_billion_capacity_is_validated_from_archive_metadata(self):
        note = self.document("saved text")
        payload = copy.deepcopy(self.store.state)
        payload["notes"][0]["body_characters"] = 1_000_000_001
        path = self.archive(payload, {note["document_id"]: "saved text"})
        target = self.target()
        with self.assertRaisesRegex(ValueError, "reference"):
            prepare_backup({"store": target, "path": path})
        self.assertEqual(target.documents.total_characters(), 0)

    def test_cancelled_export_keeps_existing_file_and_removes_temporary_output(self):
        self.document("a" * 24000)
        path = self.root / "backup.zip"
        path.write_bytes(b"Previous backup")
        cancel = threading.Event()
        original = self.store.documents.iter_text

        def cancel_after_chunk(identifier):
            for text in original(identifier):
                yield text
                cancel.set()

        with patch.object(self.store.documents, "iter_text", side_effect=cancel_after_chunk):
            with self.assertRaises(BackupCancelled):
                export_backup(self.store, path, cancel)
        self.assertEqual(path.read_bytes(), b"Previous backup")
        self.assertEqual(list(self.root.glob(".jeffery-backup-*")), [])

    def test_quota_failure_rolls_back_all_new_documents_and_existing_notes(self):
        first = self.document("a" * 40, "First")
        second = self.document("b" * 40, "Second")
        path = self.archive(self.store.state, {
            first["document_id"]: "a" * 40, second["document_id"]: "b" * 40,
        })
        target = self.target()
        existing = self.document("c" * 60, "Keep existing", target)
        before = copy.deepcopy(target.state)
        with patch.object(documents, "MAX_NOTEBOOK_CHARACTERS", 100):
            with self.assertRaisesRegex(ValueError, "capacity|full"):
                prepare_backup({"store": target, "path": path})
        self.assertEqual(target.state, before)
        self.assertEqual(target.documents.total_characters(), 60)
        self.assertEqual("".join(target.documents.iter_text(existing["document_id"])), "c" * 60)

    def test_identical_restore_reuses_verified_document_when_notebook_is_full(self):
        original = self.document("a" * 100, "Keep full notebook")
        path = self.root / "backup.zip"
        export_backup(self.store, path)
        with patch.object(documents, "MAX_NOTEBOOK_CHARACTERS", 100):
            with patch.object(self.store.documents, "prepare_import", wraps=self.store.documents.prepare_import) as prepare:
                result = prepare_backup({"store": self.store, "path": path})
                prepare.assert_not_called()
            self.assertEqual(result["prepared_ids"], [])
            self.assertEqual(result["payload"]["notes"][0]["document_id"], original["document_id"])
            self.assertEqual(self.store.import_backup(result["payload"], allow_documents=True), 0)
            self.assertEqual(self.store.documents.total_characters(), 100)
        self.assertEqual("".join(self.store.documents.iter_text(original["document_id"])), "a" * 100)

    def test_reused_document_is_verified_and_never_deleted_on_cancel(self):
        original = self.document("a" * 12000, "Keep document")
        path = self.root / "backup.zip"
        export_backup(self.store, path)
        cancel = threading.Event()
        iterator = self.store.documents.iter_text

        def cancel_after_chunk(identifier):
            for text in iterator(identifier):
                yield text
                cancel.set()

        with patch.object(self.store.documents, "iter_text", side_effect=cancel_after_chunk):
            with self.assertRaises(BackupCancelled):
                prepare_backup({"store": self.store, "path": path}, cancel)
        self.assertEqual(self.store.documents.total_characters(), 12000)
        self.assertEqual("".join(self.store.documents.iter_text(original["document_id"])), "a" * 12000)

    def test_reuse_rejects_corrupt_existing_document_even_when_metadata_matches(self):
        original = self.document("Trusted text")
        path = self.root / "backup.zip"
        export_backup(self.store, path)
        with patch.object(self.store.documents, "iter_text", return_value=iter(["Altered text"])):
            with self.assertRaisesRegex(ValueError, "integrity"):
                prepare_backup({"store": self.store, "path": path})
        self.assertEqual(self.store.documents.total_characters(), len("Trusted text"))
        self.assertEqual("".join(self.store.documents.iter_text(original["document_id"])), "Trusted text")

    def test_cancelled_prepare_cleans_committed_staged_documents(self):
        first = self.document("a" * 40, "First")
        second = self.document("b" * 40, "Second")
        path = self.archive(self.store.state, {
            first["document_id"]: "a" * 40, second["document_id"]: "b" * 40,
        })
        target = self.target()
        cancel = threading.Event()
        original = target.documents.prepare_import

        def cancel_after_first(argument, event):
            prepared = original(argument, event)
            cancel.set()
            return prepared

        with patch.object(target.documents, "prepare_import", side_effect=cancel_after_first):
            with self.assertRaises(BackupCancelled):
                prepare_backup({"store": target, "path": path}, cancel)
        self.assertEqual(target.notes, [])
        self.assertEqual(target.documents.total_characters(), 0)

    def test_failed_document_integrity_keeps_previous_export(self):
        self.document("Real text")
        self.store.notes[0]["body_sha256"] = "0" * 64
        path = self.root / "backup.zip"
        path.write_bytes(b"Keep existing")
        with self.assertRaisesRegex(ValueError, "metadata"):
            export_backup(self.store, path)
        self.assertEqual(path.read_bytes(), b"Keep existing")

    def test_export_refuses_to_replace_live_storage_files(self):
        self.store.add("Keep", "Important", 0, now=100)
        original = self.store.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "storage"):
            export_backup(self.store, self.store.path)
        self.assertEqual(self.store.path.read_bytes(), original)

    def test_export_protects_notebook_temporary_file_and_sqlite_sidecars(self):
        self.store.add("Keep", "Important", 0, now=100)
        protected = [self.store.path.with_suffix(".tmp")]
        protected.extend(Path(str(self.store.documents.path) + suffix)
                         for suffix in ("", "-wal", "-shm", "-journal"))
        for path in protected:
            with self.subTest(path=path.name):
                path.write_bytes(b"Live storage")
                with self.assertRaisesRegex(ValueError, "storage"):
                    export_backup(self.store, path)
                with self.assertRaisesRegex(ValueError, "storage"):
                    export_text({"store": self.store, "path": path})
                self.assertEqual(path.read_bytes(), b"Live storage")
        self.assertEqual(list(self.store.path.parent.glob(".jeffery-backup-*")), [])


if __name__ == "__main__":
    unittest.main()
