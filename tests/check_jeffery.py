"""Notes, watcher, actual drag events, animation, and shortcut integration."""
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt, QTimer, QEvent, QPointF, QPoint
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication
from main import BuddyApp
from settings import AppSettings
from notes import NoteStore

app = QApplication([])
app.setQuitOnLastWindowClosed(False)
temp = tempfile.TemporaryDirectory()
settings = AppSettings(Path(temp.name) / "settings.json")
buddy = BuddyApp(app, settings, show_tray=False)
preview = Path(os.environ.get("BUDDY_QA_DIR", temp.name))
preview.mkdir(parents=True, exist_ok=True)
errors = []
state = {}


def mouse(widget, event_type, global_point, button, buttons):
    event = QMouseEvent(event_type, QPointF(40, 40), QPointF(global_point), button, buttons, Qt.NoModifier)
    QApplication.sendEvent(widget, event)


def initial_checks():
    try:
        assert buddy.settings["pet_name"] == "Jeffery"
        buddy.show_notepad()
        buddy.notepad.title.setText("Prepare customer order")
        buddy.notepad.body.setPlainText("Check the stock and pack the order before pickup.")
        assert buddy.notepad.save_note()
        first = buddy.notepad.editing_id
        state["first"] = first
        buddy.note_service.tick()
        assert buddy.note_popup.isVisible(), "New-note acknowledgment did not appear"
        assert buddy.note_popup.identifier == first
        assert "Prepare customer order" in buddy.pet.bubble.text
        buddy.notepad.toggle_pin()
        assert first in buddy.stickies, "Sticky note did not appear"
        buddy.stickies[first].grab().save(str(preview / "sticky-note.png"))
        buddy.note_popup.grab().save(str(preview / "reminder.png"))
        buddy.notepad.grab().save(str(preview / "notepad.png"))
        buddy.note_popup.snooze_requested.emit(first)
        assert buddy.notes_store.find(first)["next_due"] > time.time() + 295
        buddy.note_popup.hide()
        buddy.note_popup.done_requested.emit(first)
        assert buddy.notes_store.find(first)["done"]
        assert first not in buddy.stickies, "Completed sticky stayed visible"
        buddy.apply_settings({"quiet_mode": True})
        second = buddy.note_service.save_note(None, "Review website", "Review the homepage layout.", 30)
        buddy.note_service.last_delivery = -float("inf")
        buddy.note_service.tick()
        assert buddy.notes_store.find(second["id"])["unannounced"], "Quiet mode consumed a note"
        buddy.apply_settings({"quiet_mode": False})
        buddy.note_service.tick()
        assert buddy.note_popup.identifier == second["id"], "A note was skipped after quiet mode"
        buddy.note_service.delete(second["id"])
        assert not buddy.note_popup.isVisible(), "Deleted note left an orphan reminder card"
        buddy.apply_settings({"quiet_mode": True})
        linked = Path(temp.name) / "JefferyNotes.txt"
        linked.write_text("Check new orders\n", encoding="utf-8")
        buddy.note_service.link(linked)
        state["linked"] = linked
        state["before_count"] = len(buddy.notes_store.notes)
        buddy.notepad.body.setPlainText("An unsaved editor draft that must survive incoming notes.")
        replacement = linked.with_suffix(".tmp")
        replacement.write_text("Check new orders\nPack customer order\n", encoding="utf-8")
        replacement.replace(linked)
        buddy.launcher_window.label.setText("My Project")
        buddy.launcher_window.kind.setCurrentIndex(1)
        buddy.launcher_window.target.setText(temp.name)
        buddy.launcher_window.add_shortcut()
        shortcut = buddy.launcher.store.shortcuts[0]
        assert any(a.text() == "My Project" for a in buddy.quick_menu.actions())
        with patch("launcher.QDesktopServices.openUrl", return_value=True) as opened:
            buddy.launcher.launch(shortcut["id"])
            assert opened.call_args[0][0].toLocalFile() == temp.name
        buddy.show_launcher()
        app.processEvents()
        buddy.launcher_window.grab().save(str(preview / "quick-launch.png"))
        for character in ("robot", "cat", "knight"):
            buddy.character_actions[character].trigger()
            assert buddy.settings["character"] == character
            buddy.pet.perform("WAVE", 2)
            buddy.pet.grab().save(str(preview / (character + ".png")))
        for action in ("PET", "EAT", "WAVE", "DANCE", "JUMP", "SLEEP"):
            buddy.interact_actions[action].trigger()
            assert buddy.pet.state == action
        buddy.apply_settings({"character": "robot", "quiet_mode": True})
        buddy.pet.set_paused(False)
        buddy.pet.action_state = None
        start = buddy.pet.pos() + buddy.pet.rect().center()
        destination = buddy.pet.monitor_rect().topLeft() + QPoint(330, 170)
        mouse(buddy.pet, QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton)
        mouse(buddy.pet, QEvent.MouseMove, destination, Qt.NoButton, Qt.LeftButton)
        mouse(buddy.pet, QEvent.MouseButtonRelease, destination, Qt.LeftButton, Qt.NoButton)
        assert buddy.pet.drop_motion is not None, "Drag release did not open the parachute"
        assert buddy.pet.state == "PARACHUTE"
        state["drop_y"] = buddy.pet.y()
        buddy.pet.grab().save(str(preview / "parachute.png"))
        buddy.pet.perform("WAVE", 2)
        assert buddy.pet.queued_action == ("WAVE", 2), "Interaction interrupted the drop"
        QTimer.singleShot(600, finish_checks)
    except Exception as exc:
        errors.append(str(exc))
        app.quit()


def finish_checks():
    try:
        assert len(buddy.notes_store.notes) == state["before_count"] + 1, "Saved linked line was not detected"
        assert buddy.notepad.body.toPlainText().startswith("An unsaved editor draft"), "Incoming notes replaced the draft"
        assert buddy.pet.y() > state["drop_y"], "Parachute did not descend on its timer"
        for _ in range(200):
            if not buddy.pet.drop_motion:
                break
            buddy.pet.advance_drop(0.1)
        assert buddy.pet.drop_motion is None, "Parachute never landed"
        assert buddy.pet.state == "LANDING"
        assert buddy.pet.height() == buddy.settings["pet_size"], "Canopy window did not shrink after landing"
        rect = buddy.pet.monitor_rect()
        assert rect.contains(buddy.pet.geometry()), "Landing escaped the monitor"
        buddy.pet.grab().save(str(preview / "landing.png"))
        buddy.pet.action_until = time.monotonic() - 1
        buddy.pet.animate()
        assert buddy.pet.state == "WAVE", "Queued animation did not resume after landing"
        reloaded = NoteStore(buddy.notes_store.path)
        assert len(reloaded.notes) == len(buddy.notes_store.notes), "Notebook did not persist"
        buddy.menu.actions()[-1].trigger()
    except Exception as exc:
        errors.append(str(exc))
        app.quit()


QTimer.singleShot(1200, initial_checks)
QTimer.singleShot(12000, app.quit)
app.exec()
assert not buddy.thread.isRunning()
assert not buddy.note_service.timer.isActive()
assert not buddy.note_service.scan_timer.isActive()
assert not buddy.pet.timer.isActive()
temp.cleanup()
if errors:
    raise AssertionError("; ".join(errors))
print("PASS: saved-note reminders, snooze/done, sticky notes, quiet queue, linked text watcher, preserved draft, shortcuts, three characters, interactions, drag/parachute/landing, queued animation, persistence, shutdown")
