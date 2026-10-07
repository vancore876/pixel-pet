"""Actual Qt interaction with scripted Groq replies; no real key or network needed."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QObject, Signal, QTimer, Qt, QPoint, QPointF, QEvent, QUrl, QMimeData, qInstallMessageHandler
from PySide6.QtGui import QMouseEvent, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication
from main import BuddyApp
from ai_chat import ChatWindow
from credentials import CredentialStore
from settings import AppSettings


def qt_message(kind, context, message):
    if "This plugin does not support" not in message:
        print(message, file=sys.stderr)


qInstallMessageHandler(qt_message)
app = QApplication([])
app.setQuitOnLastWindowClosed(False)
temp = tempfile.TemporaryDirectory()
root = Path(temp.name)
buddy = BuddyApp(app, AppSettings(root / "settings.json"), show_tray=False)
preview = Path(os.environ.get("BUDDY_QA_DIR", root))
preview.mkdir(parents=True, exist_ok=True)
errors, state = [], {}


class ScriptedClient(QObject):
    completed = Signal(object)
    failed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.calls = []

    def send(self, model, messages, tools=True):
        self.calls.append({"model": model, "messages": json.loads(json.dumps(messages)), "tools": tools})
        self.busy_changed.emit(True)
        if tools:
            response = {"role": "assistant", "content": "", "tool_calls": [{"id": "wave_1", "type": "function", "function": {"name": "buddy_action", "arguments": '{"action":"wave"}'}}]}
        else:
            response = {"role": "assistant", "content": "Hi! I'm waving to you. Your computer readings are available when you share them.", "tool_calls": []}
        def complete():
            self.busy_changed.emit(False)
            self.completed.emit(response)
        QTimer.singleShot(40, complete)
        return True

    def cancel(self):
        self.busy_changed.emit(False)


class FakeReply(QObject):
    finished = Signal()
    def __init__(self):
        super().__init__()
        self.aborted = False
    def abort(self):
        self.aborted = True
        self.finished.emit()


def mouse(widget, kind, local, button, buttons):
    global_point = widget.mapToGlobal(local)
    QApplication.sendEvent(widget, QMouseEvent(kind, QPointF(local), QPointF(global_point), button, buttons, Qt.NoModifier))


def begin():
    try:
        buddy.apply_settings({"quiet_mode": True, "mouse_mode": "off"})
        folder = buddy.folders.add_folder(root)
        assert folder and buddy.pet.hop_motion is not None
        state["folder"] = folder["id"]
        QTimer.singleShot(400, lambda: buddy.pet.grab().save(str(preview / "folder-hop.png")))
        QTimer.singleShot(1400, hidden_checks)
    except Exception as exc:
        errors.append(repr(exc)); app.quit()


def hidden_checks():
    try:
        identifier = state["folder"]
        assert buddy.pet.hidden_in == identifier, "Timed folder hop did not enter the portal"
        assert not buddy.pet.isVisible()
        assert buddy.folders.return_timer.isActive()
        assert buddy.folders.peek_timer.isActive()
        buddy.folders.peek()
        card = buddy.folders.cards[identifier]
        assert card.occupied and card.peeking
        card.grab().save(str(preview / "folder-peek.png"))
        buddy.apply_settings({"theme": "forest"})
        assert not buddy.pet.isVisible(), "A settings change exposed the hidden buddy"
        buddy.folders.emerge()
        assert buddy.pet.isVisible() and not buddy.pet.hidden_in
        assert not card.occupied
        note = buddy.note_service.save_note(None, "Call the customer", "Private note body for context testing", 0)
        buddy.folders.enter(identifier)
        buddy.apply_settings({"quiet_mode": False})
        buddy.note_service.last_delivery = -float("inf")
        buddy.note_service.tick()
        assert not buddy.pet.hidden_in and buddy.pet.isVisible(), "A note reminder did not bring Jeffery out"
        buddy.note_action(note["id"], "done")
        buddy.apply_settings({"quiet_mode": True, "theme": "midnight"})
        # A real folder drag/drop uses an existing portal; it never moves the folder.
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(root))])
        enter = QDragEnterEvent(QPoint(30, 30), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(buddy.pet, enter)
        assert enter.isAccepted()
        drop = QDropEvent(QPointF(30, 30), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(buddy.pet, drop)
        assert drop.isAccepted()
        assert len(buddy.folders.store.folders) == 2
        buddy.pet.cancel_hop()
        buddy.folders.emerge(silent=True)
        buddy.pet.move(300, 400)
        buddy.pet.real_x, buddy.pet.real_y = 300.0, 400.0
        for mode, expected in (("chase", "CHASE"), ("shy", "SHY"), ("watch", "LOOK")):
            buddy.set_mouse_mode(mode)
            buddy.pet.action_state = None
            buddy.pet.next_state = time.monotonic() + 10
            buddy.pet.last_tick = time.monotonic() - 0.1
            position = buddy.pet.geometry().center() + QPoint(100, 15)
            before = buddy.pet.x()
            with patch("pet.QCursor.pos", return_value=position):
                buddy.pet.animate()
            assert buddy.pet.state == expected, (mode, buddy.pet.state)
            if mode == "chase":
                assert buddy.pet.x() > before
            elif mode == "shy":
                assert buddy.pet.x() < before
        buddy.pet.grab().save(str(preview / "mouse-watch.png"))
        buddy.set_mouse_mode("off")
        buddy.play_letters("JEFFERY")
        assert buddy.letters.isVisible() and buddy.letters.timer.isActive()
        assert len(buddy.letters.tiles) == 7
        buddy.letters.timer.stop()
        tile = buddy.letters.tiles[0]
        start = QPoint(round(tile.x + 15), round(tile.y + 15))
        destination = start + QPoint(90, 85)
        before = tile.x
        mouse(buddy.letters, QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton)
        mouse(buddy.letters, QEvent.MouseMove, destination, Qt.NoButton, Qt.LeftButton)
        mouse(buddy.letters, QEvent.MouseButtonRelease, destination, Qt.LeftButton, Qt.NoButton)
        assert tile.x > before + 80, "Actual letter dragging failed"
        buddy.pet.cancel_hop()
        point = buddy.letters.mapToGlobal(QPoint(round(tile.x + 22 - buddy.pet.width()/2), round(tile.y + 27 - buddy.pet.height()/2)))
        buddy.pet.move(point)
        buddy.pet.real_x, buddy.pet.real_y = float(point.x()), float(point.y())
        buddy.letters.last_tick = time.monotonic() - 0.03
        buddy.letters.tick()
        assert abs(tile.vx) + abs(tile.vy) > 0, "Jeffery did not push the letter tile"
        buddy.apply_settings({"theme": "midnight"})
        assert buddy.letters.timer.isActive(), "Changing settings froze letter physics"
        buddy.letters.grab().save(str(preview / "letter-play.png"))
        buddy.stop_play()
        assert not buddy.letters.isVisible() and not buddy.letters.timer.isActive()
        assert not buddy.pet.hidden_in and buddy.pet.hop_motion is None
        before_count = len(buddy.notes_store.notes)
        buddy.run_buddy_action({"action": "draft_note", "title": "Check project", "body": "Review the new website", "repeat_minutes": 5})
        assert buddy.notepad.dirty and buddy.notepad.body.toPlainText() == "Review the new website"
        assert len(buddy.notes_store.notes) == before_count, "AI drafted note was saved without review"
        blocked = buddy.run_buddy_action({"action": "draft_note", "body": "Replace draft"})
        assert "unsaved" in blocked
        assert buddy.notepad.body.toPlainText() == "Review the new website"
        assert buddy.notepad.save_note()
        secret = "Private note body for context testing"
        assert secret not in json.dumps(buddy.ai_context(True, False))
        active = buddy.note_service.save_note(None, "Private note", secret, 0)
        assert secret in json.dumps(buddy.ai_context(False, True))
        assert "readings" not in buddy.ai_context(False, True)
        credentials = CredentialStore(root / "test.key")
        credentials.save("gsk_" + "testplaceholder" * 3, remember=False)
        fake = ScriptedClient()
        chat = ChatWindow(buddy.settings, credentials, buddy.ai_context, buddy.run_buddy_action, client=fake)
        chat.preferences_changed.connect(buddy.persist)
        # Sharing is on by default in 5.1; this turn deliberately tests opting out.
        chat.notes.setChecked(False)
        state["fake"], state["chat"] = fake, chat
        chat.show()
        chat.input.setText("Wave to me and explain my computer readings.")
        chat.submit()
        # Verify cancellation disconnects an unfinished real Qt network reply.
        reply = FakeReply()
        buddy.chat.credentials.session_key = credentials.session_key
        with patch.object(buddy.chat.client.manager, "post", return_value=reply):
            assert buddy.chat.client.send("openai/gpt-oss-20b", [{"role": "user", "content": "Test"}])
            assert buddy.chat.client.timeout.isActive()
            buddy.chat.show()
            buddy.chat.close()
            assert reply.aborted and buddy.chat.client.reply is None
            assert not buddy.chat.client.timeout.isActive()
        QTimer.singleShot(250, finish)
    except Exception as exc:
        errors.append(repr(exc)); app.quit()


def finish():
    try:
        chat, fake = state["chat"], state["fake"]
        assert len(fake.calls) == 2, "Tool result did not complete a bounded follow-up"
        assert fake.calls[1]["tools"] is False
        assert any(m["role"] == "tool" for m in fake.calls[1]["messages"])
        assert len(chat.history) == 2
        assert "I'm waving" in chat.transcript.toPlainText()
        assert "Private note body" not in fake.calls[0]["messages"][0]["content"]
        chat.grab().save(str(preview / "groq-chat.png"))
        chat.tabs.setCurrentIndex(1)
        chat.grab().save(str(preview / "groq-connection.png"))
        fake.failed.emit("Groq's rate limit was reached. Wait a little and try again.")
        assert "rate limit" in chat.status.text()
        chat.clear_conversation()
        assert chat.history == []
        chat.hide()
        buddy.folders.enter(state["folder"])
        buddy.folders.return_timer.start(120)
        QTimer.singleShot(250, automatic_return_checks)
    except Exception as exc:
        errors.append(repr(exc)); app.quit()


def automatic_return_checks():
    try:
        assert not buddy.pet.hidden_in and buddy.pet.isVisible(), "Hide-and-seek did not return on its timer"
        assert not buddy.folders.cards[state["folder"]].occupied
        assert not buddy.folders.peek_timer.isActive()
        buddy.menu.actions()[-1].trigger()
    except Exception as exc:
        errors.append(repr(exc)); app.quit()


QTimer.singleShot(1000, begin)
QTimer.singleShot(15000, lambda: (errors.append("GUI check timed out"), app.quit()))
app.exec()
assert not buddy.thread.isRunning()
assert not buddy.letters.timer.isActive()
assert not buddy.folders.return_timer.isActive()
assert not buddy.folders.peek_timer.isActive()
assert buddy.chat.client.reply is None
temp.cleanup()
if errors:
    raise AssertionError("; ".join(errors))
print("PASS: timed folder hop/hide/peek/reveal, settings while hidden, reminder recovery, real folder drag/drop, cursor chase/shy/watch, actual letter drag and push, physics restart/stop, draft protection, context toggles, Groq tool follow-up, key-free settings, request cancellation, clean shutdown")
