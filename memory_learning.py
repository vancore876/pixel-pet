"""Local learning first, with optional evidence-checked Groq enrichment."""
from collections import deque
from PySide6.QtCore import QObject, QTimer, Signal
from ai_chat import GroqClient
from memory import extraction_prompt, contains_secret
from credentials import redact


class MemoryLearner(QObject):
    changed = Signal()
    status_changed = Signal(str)

    def __init__(self, store, settings, credentials, parent=None, client=None):
        super().__init__(parent)
        self.store, self.settings, self.credentials = store, settings, credentials
        self.client = client or GroqClient(credentials, self)
        self.queue = deque(maxlen=4)
        self.pending = None
        self.generation = 0
        self.closed = False
        self.blocked = False
        self.client.completed.connect(self.received)
        self.client.failed.connect(self.failed)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(800)
        self.timer.timeout.connect(self.pump)

    def observe(self, text):
        if self.closed or not self.settings["memory_enabled"] or contains_secret(text):
            return
        self.generation += 1
        try:
            entries = self.store.learn(text)
            if entries:
                self.changed.emit()
                self.status_changed.emit("Saved a preference in Jeffery's local memory.")
        except (OSError, ValueError) as exc:
            self.status_changed.emit(redact(exc))
            return
        if self.settings["memory_ai"] and self.settings["ai_share_memory"] and not self.blocked:
            self.queue.append((text[:4000], self.generation))
            if self.pending is None:
                self.timer.start()

    def pump(self):
        if self.closed or self.pending is not None or not self.queue:
            return
        if not all(self.settings[key] for key in ("memory_enabled", "memory_ai", "ai_share_memory")):
            self.reset()
            return
        try:
            if not self.credentials.get():
                self.queue.clear()
                return
        except (OSError, ValueError, UnicodeError):
            self.queue.clear()
            return
        self.pending = self.queue.popleft()
        if not self.client.send(self.settings["ai_model"], extraction_prompt(self.pending[0]), tools=False, json_mode=True):
            self.pending = None

    def received(self, message):
        pending, self.pending = self.pending, None
        if pending and pending[1] == self.generation and not self.closed and all(self.settings[key] for key in ("memory_enabled", "memory_ai", "ai_share_memory")):
            try:
                entries = self.store.learn_extracted(message.get("content", ""), pending[0])
                if entries:
                    self.changed.emit()
                    self.status_changed.emit("Groq helped save a preference in your local memory.")
            except (OSError, ValueError, TypeError):
                self.status_changed.emit("Local memory is available. Groq's memory suggestion was not saved.")
        if self.queue and not self.closed:
            self.timer.start(2500)

    def failed(self, message):
        self.pending = None
        self.queue.clear()
        self.blocked = "rejected" in message.lower() or "401" in message
        self.status_changed.emit("Local memory still works. " + redact(message))

    def reset(self):
        self.generation += 1
        self.timer.stop()
        self.queue.clear()
        self.pending = None
        self.client.cancel()
        self.blocked = False

    def stop(self):
        self.closed = True
        self.reset()
