"""Notification audio follows visible delivery, user preferences and burst limits."""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from config import DEFAULTS
from main import BuddyApp
from notification_sounds import NotificationSounds, _play_notification_sound
from settings import AppSettings
from settings_window import SettingsWindow

APP = QApplication.instance() or QApplication([])


class NotificationSoundTests(unittest.TestCase):
    def setUp(self):
        self.settings = dict(DEFAULTS)
        self.now = 0.0
        self.playback = Mock()
        self.sounds = NotificationSounds(self.settings, clock=lambda: self.now,
                                         playback=self.playback)

    def test_bursts_share_one_throttle_and_later_reminders_can_sound(self):
        self.assertTrue(self.sounds.play())
        self.assertFalse(self.sounds.play())
        self.now = 1.99
        self.assertFalse(self.sounds.play())
        self.now = 2.0
        self.assertTrue(self.sounds.play())
        self.now = 1802.0
        self.assertTrue(self.sounds.play())
        self.assertEqual(self.playback.call_count, 3)

    def test_quiet_mode_and_disabled_sound_do_not_consume_playback_slot(self):
        self.settings["quiet_mode"] = True
        self.assertFalse(self.sounds.play())
        self.settings.update(quiet_mode=False, notification_sounds=False)
        self.assertFalse(self.sounds.play())
        self.playback.assert_not_called()
        self.settings["notification_sounds"] = True
        self.assertTrue(self.sounds.play())

    def test_missing_audio_device_does_not_raise_or_repeatedly_retry(self):
        self.playback.side_effect = RuntimeError("No audio device")
        self.assertFalse(self.sounds.play())
        self.assertFalse(self.sounds.play())
        self.playback.assert_called_once()
        self.now = 2.0
        self.playback.side_effect = None
        self.assertTrue(self.sounds.play())

    def test_windows_uses_native_alias_asynchronously_without_default_fallback(self):
        native = SimpleNamespace(PlaySound=Mock(), SND_ALIAS=0x10000,
                                 SND_ASYNC=1, SND_NODEFAULT=2)
        with patch("notification_sounds.sys.platform", "win32"), \
                patch.dict(sys.modules, winsound=native):
            _play_notification_sound()
        native.PlaySound.assert_called_once_with("SystemNotification", 0x10003)

    def test_other_platform_uses_qt_beep_without_loading_windows_audio(self):
        with patch("notification_sounds.sys.platform", "linux"), \
                patch.object(QApplication, "beep") as beep:
            _play_notification_sound()
        beep.assert_called_once_with()

    def test_saved_preference_is_validated_and_older_settings_default_to_enabled(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text(json.dumps({"quiet_mode": True}), encoding="utf-8")
            settings = AppSettings(path)
            self.assertTrue(settings["notification_sounds"])
            settings.save({"notification_sounds": False})
            self.assertFalse(AppSettings(path)["notification_sounds"])
            self.assertTrue(AppSettings.validate({"notification_sounds": "false"})[
                "notification_sounds"])

    def test_settings_sound_toggle_remains_a_draft_until_apply(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = AppSettings(Path(folder) / "settings.json")
            window = SettingsWindow(settings)
            try:
                window.refresh(0)
                control = window.controls["notification_sounds"]
                self.assertTrue(control.isChecked())
                control.setChecked(False)
                self.assertTrue(settings["notification_sounds"])
                changes = []
                window.apply_requested.connect(changes.append)
                window.apply()
                self.assertFalse(changes[-1]["notification_sounds"])
                self.assertTrue(settings["notification_sounds"])
                settings.save(changes[-1])
                self.assertFalse(AppSettings(settings.path)["notification_sounds"])
            finally:
                window.close()
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


class NotificationSoundDeliveryTests(unittest.TestCase):
    routes = (("deliver_work_notification", "work_can_notify", "work_popup", "work_notifications", "present"),
              ("deliver_team_notification", "team_can_notify", "team_popup", "team", "present"),
              ("deliver_order_escalation", "order_can_notify", "overdue_popup", "order_escalations", "display"))

    def fixture(self, route, *, allowed=True, shown=True, accepted=True, visible=True):
        method, allowed_name, popup_name, service_name, show_name = route
        playback = Mock()
        settings = dict(DEFAULTS)
        fixture = SimpleNamespace(settings=settings, pet=Mock(), shutting_down=False,
                                  notification_sounds=NotificationSounds(settings, playback=playback))
        setattr(fixture, allowed_name, Mock(return_value=allowed))
        popup, service = Mock(), Mock()
        getattr(popup, show_name).return_value = shown
        popup.isVisible.return_value = visible
        service.mark_notification_presented.return_value = accepted
        setattr(fixture, popup_name, popup)
        setattr(fixture, service_name, service)
        return fixture, playback, popup, service

    def test_personal_team_and_reminder_delivery_chime_after_acceptance(self):
        for route in self.routes:
            for kind in ("received", "reminder"):
                with self.subTest(route=route[0], kind=kind):
                    fixture, playback, popup, service = self.fixture(route)
                    notification = {"id": "42", "notification_kind": kind}
                    events = []
                    service.mark_notification_presented.side_effect = lambda value: events.append("accepted") or True
                    playback.side_effect = lambda: events.append("sound")
                    getattr(BuddyApp, route[0])(fixture, notification)
                    self.assertEqual(events, ["accepted", "sound"])
                    service.mark_notification_presented.assert_called_once_with(notification)
                    service.release_notification.assert_not_called()

    def test_blocked_or_unshown_popups_never_play_or_mark_presented(self):
        for route in self.routes:
            for values in ({"allowed": False}, {"shown": False}):
                with self.subTest(route=route[0], values=values):
                    fixture, playback, popup, service = self.fixture(route, **values)
                    notification = {"id": "42"}
                    getattr(BuddyApp, route[0])(fixture, notification)
                    playback.assert_not_called()
                    service.mark_notification_presented.assert_not_called()
                    service.release_notification.assert_called_once_with(notification)

    def test_rejected_or_no_longer_visible_delivery_does_not_chime(self):
        for route in self.routes:
            for values in ({"accepted": False}, {"visible": False}):
                with self.subTest(route=route[0], values=values):
                    fixture, playback, popup, service = self.fixture(route, **values)
                    getattr(BuddyApp, route[0])(fixture, {"id": "42"})
                    playback.assert_not_called()

    def test_quiet_mode_toggle_and_audio_failure_do_not_disrupt_message_delivery(self):
        route = self.routes[0]
        for preference in ("quiet_mode", "notification_sounds", "broken_device"):
            with self.subTest(preference=preference):
                fixture, playback, popup, service = self.fixture(route)
                if preference == "broken_device":
                    playback.side_effect = OSError("Audio unavailable")
                else:
                    fixture.settings[preference] = preference == "quiet_mode"
                getattr(BuddyApp, route[0])(fixture, {"id": "42"})
                service.mark_notification_presented.assert_called_once()
                popup.present.assert_called_once()
                if preference != "broken_device":
                    playback.assert_not_called()

    def note_fixture(self, visible=True):
        fixture = SimpleNamespace(settings=dict(DEFAULTS), shutting_down=False,
                                  pet=Mock(), notes_store=Mock(), note_service=Mock(),
                                  note_popup=Mock(), smart_notes=Mock(), notepad=Mock())
        fixture.pet.hidden_in = None
        fixture.notes_store.find.return_value = None
        fixture.note_popup.isVisible.return_value = visible
        fixture.note_popup.identifier = "note-42"
        playback = Mock()
        fixture.notification_sounds = NotificationSounds(fixture.settings, playback=playback)
        return fixture, playback

    def test_new_notes_and_reminders_sound_only_after_visible_popup(self):
        note = {"id": "note-42", "title": "Customer follow-up", "body": "Call tomorrow"}
        for kind in ("added", "reminder"):
            for visible in (True, False):
                with self.subTest(kind=kind, visible=visible):
                    fixture, playback = self.note_fixture(visible)
                    with patch("main.current_guidance", return_value={}), \
                            patch("main.fallback_reminder", return_value="Customer follow-up"):
                        BuddyApp.deliver_note(fixture, note, kind)
                    fixture.note_popup.present.assert_called_once()
                    self.assertEqual(playback.call_count, int(visible))

    def test_later_ai_reminder_text_update_does_not_replay_sound(self):
        fixture, playback = self.note_fixture()
        note = {"id": "note-42"}
        with patch("main.current_guidance", return_value={"reminder": "Updated reminder"}):
            BuddyApp.smart_note_ready(fixture, note)
        fixture.note_popup.update_guidance.assert_called_once_with(note)
        playback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
