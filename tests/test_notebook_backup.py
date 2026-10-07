import copy
import errno
import hashlib
import json
import sqlite3
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import warnings
import zipfile

import documents
from content_intake import cleanup_result
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

    def revised_archive(self, current, text, *, updated=200, name="revised.zip"):
        payload = copy.deepcopy(self.store.state)
        revised = next(note for note in payload["notes"] if note["id"] == current["id"])
        revised.update(document_id="e" * 32, body=text[:10000], body_characters=len(text),
            body_sha256=hashlib.sha256(text.encode()).hexdigest(), updated=updated)
        bodies = {note["document_id"]: "".join(self.store.documents.iter_text(note["document_id"]))
                  for note in payload["notes"] if note.get("document_id") and note["id"] != current["id"]}
        bodies[revised["document_id"]] = text
        return self.archive(payload, bodies, name=name)

    def test_newer_document_restore_counts_replacement_once_at_capacity(self):
        original = copy.deepcopy(self.document("a" * 150))
        path = self.revised_archive(original, "b" * 175)
        with patch.object(documents, "MAX_NOTEBOOK_CHARACTERS", 200):
            result = prepare_backup({"store": self.store, "path": path})
            self.assertEqual("".join(self.store.documents.iter_text(original["document_id"])), "a" * 150)
            self.assertEqual(self.store.notes[0]["document_id"], original["document_id"])
            self.assertEqual(self.store.import_backup(result["payload"], allow_documents=True), 1)
            self.assertEqual(self.store.character_count(), 175)
            self.assertEqual(self.store.documents.total_characters(), 175)
            self.assertEqual("".join(self.store.documents.iter_text(self.store.notes[0]["document_id"])), "b" * 175)

    def test_shared_old_document_is_not_credited_when_another_note_keeps_it(self):
        original = copy.deepcopy(self.document("a" * 150))
        shared = copy.deepcopy(original)
        shared["id"] = "shared-note"
        self.store.notes.append(shared)
        self.store.save()
        path = self.revised_archive(original, "b" * 175)
        before = copy.deepcopy(self.store.state)
        with patch.object(documents, "MAX_NOTEBOOK_CHARACTERS", 200):
            with self.assertRaisesRegex(ValueError, "capacity|full|limit"):
                prepare_backup({"store": self.store, "path": path})
        self.assertEqual(self.store.state, before)
        self.assertEqual(self.store.documents.total_characters(), 150)

    def test_older_document_backup_does_not_use_replacement_capacity(self):
        original = copy.deepcopy(self.document("a" * 150))
        path = self.revised_archive(original, "b" * 175, updated=99)
        with patch.object(documents, "MAX_NOTEBOOK_CHARACTERS", 200):
            result = prepare_backup({"store": self.store, "path": path})
            self.assertEqual(result["prepared_ids"], [])
            self.assertEqual(self.store.import_backup(result["payload"], allow_documents=True), 0)
            self.assertEqual(self.store.documents.total_characters(), 150)
        self.assertEqual("".join(self.store.documents.iter_text(original["document_id"])), "a" * 150)

    def test_cancelled_replacement_keeps_original_text_and_releases_batch(self):
        original = copy.deepcopy(self.document("a" * 150))
        path = self.revised_archive(original, "b" * 175)
        cancel = threading.Event()
        prepare = self.store.documents.prepare_import
        def cancel_after_stage(argument, event):
            result = prepare(argument, event)
            cancel.set()
            return result
        with patch.object(documents, "MAX_NOTEBOOK_CHARACTERS", 200), \
                patch.object(self.store.documents, "prepare_import", side_effect=cancel_after_stage):
            with self.assertRaises(BackupCancelled):
                prepare_backup({"store": self.store, "path": path}, cancel)
        self.assertEqual(self.store.documents.total_characters(), 150)
        self.assertEqual("".join(self.store.documents.iter_text(original["document_id"])), "a" * 150)
        with sqlite3.connect(self.store.documents.path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM restore_batches").fetchone()[0], 0)

    def test_json_write_failure_rolls_back_notes_and_cleanup_keeps_original(self):
        original = copy.deepcopy(self.document("a" * 150))
        path = self.revised_archive(original, "b" * 175)
        before, before_file = copy.deepcopy(self.store.state), self.store.path.read_bytes()
        with patch.object(documents, "MAX_NOTEBOOK_CHARACTERS", 200):
            result = prepare_backup({"store": self.store, "path": path})
            with patch("notes.os.replace", side_effect=OSError("read-only folder")):
                with self.assertRaises(OSError):
                    self.store.import_backup(result["payload"], allow_documents=True)
            cleanup_result(result)
        self.assertEqual(self.store.state, before)
        self.assertEqual(self.store.path.read_bytes(), before_file)
        self.assertEqual(self.store.documents.total_characters(), 150)
        self.assertEqual("".join(self.store.documents.iter_text(original["document_id"])), "a" * 150)

    def test_added_document_between_staging_and_commit_rejects_projected_overflow(self):
        original = copy.deepcopy(self.document("a" * 150))
        path = self.revised_archive(original, "b" * 175)
        with patch.object(documents, "MAX_NOTEBOOK_CHARACTERS", 200):
            result = prepare_backup({"store": self.store, "path": path})
            added = self.document("c" * 50, "Concurrent document")
            before, before_file = copy.deepcopy(self.store.state), self.store.path.read_bytes()
            with self.assertRaisesRegex(ValueError, "capacity"):
                self.store.import_backup(result["payload"], allow_documents=True)
            cleanup_result(result)
            self.assertEqual(self.store.documents.total_characters(), 200)
        self.assertEqual(self.store.state, before)
        self.assertEqual(self.store.path.read_bytes(), before_file)
        self.assertTrue(self.store.documents.exists(original["document_id"]))
        self.assertTrue(self.store.documents.exists(added["document_id"]))

    def test_added_shared_reference_after_staging_preserves_old_document(self):
        original = copy.deepcopy(self.document("a" * 150))
        path = self.revised_archive(original, "b" * 175)
        with patch.object(documents, "MAX_NOTEBOOK_CHARACTERS", 200):
            result = prepare_backup({"store": self.store, "path": path})
            shared = copy.deepcopy(original)
            shared["id"] = "concurrent-shared-note"
            self.store.notes.append(shared)
            self.store.save()
            before = copy.deepcopy(self.store.state)
            with self.assertRaisesRegex(ValueError, "capacity"):
                self.store.import_backup(result["payload"], allow_documents=True)
            cleanup_result(result)
        self.assertEqual(self.store.state, before)
        self.assertEqual(self.store.documents.total_characters(), 150)
        self.assertTrue(self.store.documents.exists(original["document_id"]))

    def test_new_inline_note_after_staging_is_counted_at_final_commit(self):
        original = copy.deepcopy(self.document("a" * 150))
        path = self.revised_archive(original, "b" * 175)
        with patch.object(documents, "MAX_NOTEBOOK_CHARACTERS", 200):
            result = prepare_backup({"store": self.store, "path": path})
            self.store.add("New inline note", "c" * 50, 0, now=210)
            before = copy.deepcopy(self.store.state)
            with self.assertRaisesRegex(ValueError, "capacity"):
                self.store.import_backup(result["payload"], allow_documents=True)
            cleanup_result(result)
        self.assertEqual(self.store.state, before)
        self.assertEqual(self.store.character_count(), 200)
        self.assertTrue(self.store.documents.exists(original["document_id"]))

    def test_restart_before_json_commit_discards_only_uncommitted_staging(self):
        original = copy.deepcopy(self.document("a" * 150))
        path = self.revised_archive(original, "b" * 175)
        with patch.object(documents, "MAX_NOTEBOOK_CHARACTERS", 200):
            result = prepare_backup({"store": self.store, "path": path})
            staged_id = result["prepared_ids"][0]
            self.store.close()
            reopened = NoteStore(self.store.path)
            self.targets.append(reopened)
            self.assertEqual(reopened.documents.total_characters(), 150)
            self.assertFalse(reopened.documents.exists(staged_id))
            self.assertTrue(reopened.documents.exists(original["document_id"]))

    def test_first_restore_persists_empty_baseline_for_precommit_restart(self):
        self.document("The first document")
        path = self.root / "first.zip"
        export_backup(self.store, path)
        target = self.target()
        self.assertFalse(target.path.exists())
        result = prepare_backup({"store": target, "path": path})
        self.assertEqual(json.loads(target.path.read_text())["notes"], [])
        target.close()
        reopened = NoteStore(target.path)
        self.targets.append(reopened)
        self.assertEqual(reopened.documents.total_characters(), 0)
        self.assertFalse(reopened.documents.exists(result["prepared_ids"][0]))
        retry = prepare_backup({"store": reopened, "path": path})
        self.assertEqual(reopened.import_backup(retry["payload"], allow_documents=True), 1)

    def test_create_only_restore_baseline_never_overwrites_concurrent_first_note(self):
        import notebook_backup
        target = self.target()
        link = notebook_backup.os.link
        def gui_save_before_baseline_install(source, destination):
            target.add("First saved task", "Keep this task", 0, now=100)
            return link(source, destination)
        with patch("notebook_backup.os.link", side_effect=gui_save_before_baseline_install):
            notebook_backup._ensure_restore_baseline(target)
        saved = json.loads(target.path.read_text())["notes"]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["body"], "Keep this task")
        self.assertEqual(target.notes, saved)
        self.assertEqual(list(target.path.parent.glob(".jeffery-backup-*")), [])

    def test_windows_no_hardlink_fallback_installs_complete_baseline_without_posix_overwrite(self):
        import notebook_backup
        target = self.target()
        rename = notebook_backup.os.rename
        def windows_rename(source, destination):
            if Path(destination).exists():
                raise FileExistsError(str(destination))
            return rename(source, destination)
        with patch.object(notebook_backup, "_WINDOWS_CREATE_ONLY_RENAME", True), \
                patch("notebook_backup.os.link", side_effect=OSError(errno.ENOTSUP, "no hard links")), \
                patch("notebook_backup.os.rename", side_effect=windows_rename) as fallback:
            notebook_backup._ensure_restore_baseline(target)
            fallback.assert_called_once()
        self.assertEqual(json.loads(target.path.read_text()), target.state)
        self.assertEqual(list(target.path.parent.glob(".jeffery-backup-*")), [])
        other = NoteStore(self.root / "posix" / "notes.json")
        self.targets.append(other)
        with patch.object(notebook_backup, "_WINDOWS_CREATE_ONLY_RENAME", False), \
                patch("notebook_backup.os.link", side_effect=OSError(errno.ENOTSUP, "no hard links")), \
                patch("notebook_backup.os.rename") as forbidden:
            with self.assertRaises(OSError):
                notebook_backup._ensure_restore_baseline(other)
            forbidden.assert_not_called()
        self.assertFalse(other.path.exists())

    def test_windows_no_hardlink_fallback_preserves_concurrent_first_gui_save(self):
        import notebook_backup
        target = self.target()
        def gui_save_before_windows_rename(source, destination):
            target.add("First saved task", "Keep this task", 0, now=100)
            if Path(destination).exists():
                raise FileExistsError(str(destination))
            raise AssertionError("GUI save did not create the destination")
        with patch.object(notebook_backup, "_WINDOWS_CREATE_ONLY_RENAME", True), \
                patch("notebook_backup.os.link", side_effect=OSError(errno.ENOTSUP, "no hard links")), \
                patch("notebook_backup.os.rename", side_effect=gui_save_before_windows_rename) as fallback:
            notebook_backup._ensure_restore_baseline(target)
            fallback.assert_called_once()
        saved = json.loads(target.path.read_text())["notes"]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["body"], "Keep this task")
        self.assertEqual(target.notes, saved)
        self.assertEqual(list(target.path.parent.glob(".jeffery-backup-*")), [])

    def test_busy_activation_after_json_commit_and_late_cleanup_are_restart_safe(self):
        import notes
        original = copy.deepcopy(self.document("a" * 150))
        path = self.revised_archive(original, "b" * 175)
        writer = sqlite3.connect(self.store.documents.path, isolation_level=None)
        replace = notes.os.replace
        def lock_after_json_replacement(source, destination):
            replace(source, destination)
            writer.execute("BEGIN IMMEDIATE")
        try:
            with patch.object(documents, "MAX_NOTEBOOK_CHARACTERS", 200):
                result = prepare_backup({"store": self.store, "path": path})
                staged_id = result["prepared_ids"][0]
                started = time.monotonic()
                with patch("notes.os.replace", side_effect=lock_after_json_replacement):
                    self.assertEqual(self.store.import_backup(result["payload"], allow_documents=True), 1)
                self.assertLess(time.monotonic() - started, 1)
                self.assertEqual(json.loads(self.store.path.read_text())["notes"][0]["document_id"], staged_id)
                cleanup_result(result)
                self.assertTrue(self.store.documents.exists(staged_id))
                self.assertTrue(self.store.documents.exists(original["document_id"]))
                writer.execute("ROLLBACK")
                self.store.close()
                reopened = NoteStore(self.store.path)
                self.targets.append(reopened)
                self.assertEqual(reopened.documents.total_characters(), 175)
                self.assertEqual("".join(reopened.documents.iter_text(staged_id)), "b" * 175)
                self.assertFalse(reopened.documents.exists(original["document_id"]))
        finally:
            if writer.in_transaction:
                writer.execute("ROLLBACK")
            writer.close()

    def test_corrupt_notebook_recovery_and_later_save_preserve_all_document_text(self):
        original = copy.deepcopy(self.document("a" * 150))
        path = self.revised_archive(original, "b" * 175)
        result = prepare_backup({"store": self.store, "path": path})
        staged_id = result["prepared_ids"][0]
        self.store.close()
        self.store.path.write_text("{broken", encoding="utf-8")
        reopened = NoteStore(self.store.path)
        self.targets.append(reopened)
        self.assertTrue(reopened.documents.exists(original["document_id"]))
        self.assertTrue(reopened.documents.exists(staged_id))
        reopened.add("Fresh note", "Unrelated", 0, now=300)
        self.assertTrue(reopened.documents.exists(original["document_id"]))
        self.assertTrue(reopened.documents.exists(staged_id))

    def test_transient_startup_writer_keeps_committed_recovery_retryable(self):
        original = copy.deepcopy(self.document("a" * 150))
        path = self.revised_archive(original, "b" * 175)
        result = prepare_backup({"store": self.store, "path": path})
        self.store.path.write_text(json.dumps(result["payload"]), encoding="utf-8")
        staged_id = result["prepared_ids"][0]
        self.store.close()
        writer = sqlite3.connect(self.store.documents.path, isolation_level=None)
        try:
            writer.execute("BEGIN IMMEDIATE")
            with patch.object(documents.DocumentStore, "_schedule_cleanup"):
                reopened = NoteStore(self.store.path)
                self.targets.append(reopened)
                self.assertTrue(reopened.documents.exists(staged_id))
                self.assertTrue(reopened._restore_recovery_pending)
                writer.execute("ROLLBACK")
                self.assertEqual(reopened.documents.total_characters(), 175)
                self.assertFalse(reopened._restore_recovery_pending)
                self.assertFalse(reopened.documents.exists(original["document_id"]))
        finally:
            if writer.in_transaction:
                writer.execute("ROLLBACK")
            writer.close()

    def test_blocked_corrupt_metadata_never_deletes_staged_or_original_documents(self):
        original = copy.deepcopy(self.document("a" * 150))
        path = self.revised_archive(original, "b" * 175)
        result = prepare_backup({"store": self.store, "path": path})
        staged_id = result["prepared_ids"][0]
        self.store.close()
        self.store.path.write_text("{broken", encoding="utf-8")
        with patch.object(Path, "replace", side_effect=OSError("read-only folder")):
            reopened = NoteStore(self.store.path)
        self.targets.append(reopened)
        self.assertTrue(reopened.blocked_write)
        self.assertTrue(reopened.documents.exists(staged_id))
        self.assertTrue(reopened.documents.exists(original["document_id"]))
        with self.assertRaises(OSError):
            reopened.add("Fresh note", "Unrelated", 0, now=300)
        self.assertTrue(reopened.documents.exists(staged_id))
        self.assertTrue(reopened.documents.exists(original["document_id"]))

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
