"""A quiet-mode-aware chime for displayed notifications, without audio assets."""
from __future__ import annotations

import logging
import sys
import time


def _play_notification_sound():
    if sys.platform == "win32":
        import winsound
        winsound.PlaySound("SystemNotification",
                           winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    else:
        from PySide6.QtWidgets import QApplication
        QApplication.beep()


class NotificationSounds:
    """Share one burst limit across chat, notebook and customer reminders."""

    def __init__(self, settings, *, clock=time.monotonic, playback=None):
        self.settings = settings
        self._clock = clock
        self._playback = playback or _play_notification_sound
        self._last_played = None

    def play(self):
        if self.settings["quiet_mode"] or not self.settings["notification_sounds"]:
            return False
        now = self._clock()
        if self._last_played is not None and now - self._last_played < 2.0:
            return False
        # Failed playback is throttled too, so a missing device cannot cause
        # repeated attempts while a group of reminders is being displayed.
        self._last_played = now
        try:
            self._playback()
        except Exception:
            logging.getLogger(__name__).debug("Notification sound unavailable", exc_info=True)
            return False
        return True
