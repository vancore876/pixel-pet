"""Isolated chat usability review with synthetic data and optional screenshots.

Run with the desktop interpreter. No user credentials or app files are read.
Set JEFFERY_UI_SCREENSHOT_DIR to keep the review images.
"""
import os
from pathlib import Path
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton
from ai_chat import ChatWindow
from settings import AppSettings
from test_work_chat import APP, ScriptedApi, message
from work_chat import WorkChatWindow

if APP.platformName() == "offscreen" and Path("C:/Windows/Fonts/segoeui.ttf").is_file():
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeuib.ttf")


class Credentials:
    def get(self):
        return ""


class Client(QObject):
    completed = Signal(object)
    failed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.reply = None
        self.calls = []

    def send(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return True

    def cancel(self):
        pass


def capture(window, name):
    APP.processEvents()
    QTest.qWait(300)  # Let the transcript reflow and its first line settle.
    folder = os.environ.get("JEFFERY_UI_SCREENSHOT_DIR")
    if folder:
        target = Path(folder).resolve()
        target.mkdir(parents=True, exist_ok=True)
        assert window.grab().save(str(target / f"{name}.png"))


def main():
    with tempfile.TemporaryDirectory(prefix="jeffery-chat-ui-") as folder:
        settings = AppSettings(Path(folder) / "settings.json")
        work = WorkChatWindow(settings)
        work.api = api = ScriptedApi()
        client = Client()
        ai = ChatWindow(settings, Credentials(), lambda *_: {}, lambda *_: "Wave ready", client=client)
        try:
            work.show()
            work.server.setText("192.168.50.194")
            capture(work, "coworkers-signin")
            work.signup.setChecked(True)
            capture(work, "coworkers-signup")
            assert work.confirm_password.isVisible()
            work.resize(720, 580)
            capture(work, "coworkers-signup-small")
            assert work.password.height() >= 34
            assert work.confirm_password.height() >= 34
            work.auth_scroll.ensureWidgetVisible(work.auth_button)
            capture(work, "coworkers-signup-small-scrolled")
            work.resize(960, 730)
            work.signup.setChecked(False)
            work.username.setText("alice")
            work.password.setText("synthetic-office-password")
            work.authenticate()
            api.finish(api.pending("/api/auth/login"), {"token": "synthetic-token", "user": {"id": 1, "username": "alice"}})
            api.finish(api.pending("/api/users"), {"users": [{"id": 2, "username": "bob"}, {"id": 3, "username": "charlie"}]})
            api.finish(api.pending("/api/messages"), {"messages": [message(1, "The brake pads are ready for pickup.")], "has_more": False})
            work.people_search.setText("bob")
            assert not work.conversations.item(0).isHidden()
            assert not work.conversations.item(1).isHidden()
            assert work.conversations.item(2).isHidden()
            work.conversations.setCurrentRow(1)
            assert work._peer_id == 2
            assert "Direct messages stay separate" in work.conversation_hint.text()
            api.finish(api.pending("/api/messages"), {"messages": [message(2, "Can you check the part number on this order?", recipient=1)], "has_more": False})
            work.composer.setPlainText("I will check it now.")
            work.people_search.clear()
            capture(work, "coworkers-chat")
            work.resize(720, 580)
            capture(work, "coworkers-small")
            assert work.send_button.isVisible()
            assert work.send_button.geometry().width() >= work.send_button.minimumSizeHint().width()
            settings.values["interface_appearance"] = "dark"
            work.configure()
            capture(work, "coworkers-dark")
            settings.values["interface_appearance"] = "light"
            ai.configure()
            ai.show()
            assert ai.tabs.currentIndex() == 1  # Missing key has a clear setup route.
            capture(ai, "assistant-setup")
            ai.advanced_toggle.click()
            assert ai.advanced_panel.isVisible()
            ai.notes.setChecked(False)
            ai.advanced_toggle.click()
            assert not ai.notes.isChecked()  # Collapsing preferences never changes privacy.
            ai.tabs.setCurrentIndex(0)
            capture(ai, "assistant-empty")
            prompt = next(button for button in ai.findChildren(QPushButton) if button.text() == "Draft a reply")
            prompt.click()
            assert ai.input.text().startswith("Help me draft")
            assert not client.calls  # Suggestions remain drafts until explicitly sent.
            ai._append_message("You: Help me draft a pickup message.")
            ai._append_message("Jeffery: Your parts are ready for pickup. Please bring your order reference when you visit Famous Twins.")
            capture(ai, "assistant-chat")
            ai.resize(640, 560)
            capture(ai, "assistant-small")
            assert ai.send_button.isVisible()
            settings.values["interface_appearance"] = "dark"
            ai.configure()
            capture(ai, "assistant-dark")
            home = []
            work.home_requested.connect(lambda: home.append("work"))
            ai.home_requested.connect(lambda: home.append("ai"))
            for window in (work, ai):
                next(button for button in window.findChildren(QPushButton) if button.text() == "‹ Home").click()
            assert home == ["work", "ai"]
            print("PASS: sign-in/sign-up, coworker search and direct context, deliberate AI prompt drafts, privacy disclosure, Home navigation, light/dark and compact chat layouts")
        finally:
            work.shutdown()
            ai.shutdown()
            work.close()
            ai.close()
            APP.processEvents()


if __name__ == "__main__":
    main()
