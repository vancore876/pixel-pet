import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from notes import NoteStore
from launcher import ShortcutStore, start_app
from motion import ParachuteMotion


class NotesTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "notes.json"
        self.store = NoteStore(self.path)

    def tearDown(self):
        self.folder.cleanup()

    def test_each_added_note_and_repeat_are_durable(self):
        one = self.store.add("Website", "Finish the landing page", repeat=5, now=100)
        two = self.store.add("Call", "Call back the customer", repeat=0, now=101)
        self.assertEqual(self.store.next_notification(102)[0]["id"], one["id"])
        self.store.acknowledge(one["id"], "added", 102)
        self.assertEqual(self.store.next_notification(102)[0]["id"], two["id"])
        self.store.acknowledge(two["id"], "added", 102)
        reloaded = NoteStore(self.path)
        self.assertIsNone(reloaded.next_notification(399)[0])
        note, kind = reloaded.next_notification(400)
        self.assertEqual((note["id"], kind), (one["id"], "reminder"))
        reloaded.acknowledge(note["id"], kind, 410)
        self.assertEqual(reloaded.find(one["id"])["next_due"], 710)

    def test_snooze_completion_and_pin_position(self):
        n = self.store.add("Pay attention", "A note", now=100)
        self.store.snooze(n["id"], minutes=5, now=200)
        self.assertIsNone(self.store.next_notification(499)[0])
        self.assertEqual(self.store.next_notification(500)[0]["id"], n["id"])
        self.store.modify(n["id"], pinned=True, pin_position=[-1300, 150])
        restored = NoteStore(self.path).find(n["id"])
        self.assertTrue(restored["pinned"])
        self.assertEqual(restored["pin_position"], [-1300, 150])
        self.store.complete(n["id"])
        self.assertIsNone(self.store.next_notification(100000)[0])

    def test_linked_lines_duplicates_and_relink(self):
        linked = Path(self.folder.name) / "notes.txt"
        linked.write_text("one\none\n", encoding="utf-8")
        self.store.link_file(linked)
        self.assertEqual(len(self.store.import_text("one\none\n", now=100)), 2)
        self.assertEqual(self.store.import_text("one\none\n", now=200), [])
        self.store.link_file(linked)
        self.assertEqual(self.store.import_text("one\none\n", now=200), [])
        reloaded = NoteStore(self.path)
        self.assertEqual(len(reloaded.import_text("one\none\ntwo\n", now=300)), 1)
        self.assertEqual(reloaded.import_text("one\ntwo\n", now=400), [])
        self.assertEqual(len(reloaded.notes), 3, "Removing a linked line deleted an existing note")

    def test_overdue_restart_delivers_once_then_reschedules(self):
        n = self.store.add("A reminder", "Content", repeat=30, now=100)
        self.store.acknowledge(n["id"], "added", 100)
        store = NoteStore(self.path)
        note, kind = store.next_notification(20000)
        store.acknowledge(note["id"], kind, 20000)
        self.assertIsNone(store.next_notification(20001)[0])
        self.assertEqual(store.find(n["id"])["next_due"], 21800)

    def test_atomic_failure_rolls_back_in_memory(self):
        self.store.add("Existing", "Keep me", now=100)
        with patch.object(self.store, "save", side_effect=OSError("Read-only")):
            with self.assertRaises(OSError):
                self.store.add("New", "Do not pretend this saved", now=200)
        self.assertEqual(len(self.store.notes), 1)
        self.assertEqual(len(NoteStore(self.path).notes), 1)

    def test_corruption_is_preserved_and_invalid_times_rejected(self):
        self.path.write_text("{broken", encoding="utf-8")
        store = NoteStore(self.path)
        self.assertTrue(store.warning)
        self.assertTrue(self.path.with_suffix(".corrupt.json").exists())
        row = NoteStore.validate_note({"id": "id", "title": "A note", "next_due": float("nan")})
        self.assertIsNone(row["next_due"])


class MotionTests(unittest.TestCase):
    def test_soft_descent_lands_without_overshoot(self):
        motion = ParachuteMotion(100, 600)
        for _ in range(200):
            landed = motion.step(0.1)
            self.assertLessEqual(motion.y, 600)
            self.assertLessEqual(motion.velocity, 130)
            if landed:
                break
        self.assertTrue(landed)
        self.assertEqual(motion.y, 600)

    def test_delayed_tick_is_bounded(self):
        motion = ParachuteMotion(0, 1000)
        motion.step(60)
        self.assertLess(motion.y, 20)


class LauncherTests(unittest.TestCase):
    def test_saved_shortcuts_and_unsafe_schemes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "shortcuts.json"
            store = ShortcutStore(path)
            item = store.add("Project", "folder", folder)
            self.assertEqual(ShortcutStore(path).shortcuts[0]["id"], item["id"])
            with self.assertRaises(ValueError):
                store.add("Bad", "url", "javascript:alert(1)")
            with self.assertRaises(ValueError):
                store.add("Script", "app", str(Path(folder) / "launch.cmd"))

    def test_app_path_with_spaces_is_not_shell_text(self):
        with patch("launcher.QProcess.startDetached", return_value=(True, 1234)) as start:
            start_app("C:/Program Files/My App/app.exe", ["C:/My Notes/a note.txt"])
        start.assert_called_once_with("C:/Program Files/My App/app.exe", ["C:/My Notes/a note.txt"])
