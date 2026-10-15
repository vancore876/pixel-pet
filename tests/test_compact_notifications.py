"""Notification cards stay small, passive, and reversible while expanding."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, Qt
from PySide6.QtWidgets import QApplication

from config import DEFAULTS
from team_popup import TeamPopup
from work_notification_popup import WorkNotificationPopup


APP = QApplication.instance() or QApplication([])


class CompactNotificationTests(unittest.TestCase):
    def setUp(self):
        self.settings = dict(DEFAULTS, quiet_mode=False, work_chat_notifications=True,
            notification_size="compact")
        self.popup = WorkNotificationPopup(self.settings)
        self.actions = []
        self.popup.open_requested.connect(lambda value: self.actions.append(("open", value)))
        self.popup.acknowledge_requested.connect(lambda value: self.actions.append(("ack", value)))
        self.popup.snooze_requested.connect(lambda value, minutes: self.actions.append(("later", value, minutes)))

    def tearDown(self):
        self.popup.shutdown()
        self.popup.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    @staticmethod
    def message(identifier=21, **updates):
        return dict({"id": identifier, "sender_username": "Bob", "recipient_id": 1,
            "peer_id": 2, "body": "Confirm pickup before 3 PM.", "notification_kind": "received"},
            **updates)

    def test_long_incoming_message_stays_small_with_full_read_only_text(self):
        body = "<b>請確認取貨</b> & customer stock 📦\n" * 300
        self.assertTrue(self.popup.present(self.message(body=body, sender_username="Bob" * 300)))
        APP.processEvents()
        self.assertLessEqual(self.popup.width(), 360)
        self.assertLessEqual(self.popup.height(), 210)
        self.assertFalse(self.popup.expanded)
        self.assertTrue(self.popup.preview.isVisible())
        self.assertEqual(self.popup.preview.textFormat(), Qt.PlainText)
        self.assertTrue(self.popup.body.isReadOnly())
        self.assertEqual(self.popup.body.toPlainText(), body.strip())
        self.assertEqual(self.popup.preview.text(), body.strip())
        self.assertFalse(self.popup.body.isVisible())
        self.assertTrue(self.popup.open_button.isVisible())
        self.assertTrue(self.popup.dismiss_button.isVisible())
        self.assertTrue(self.popup.more_button.isVisible())
        self.assertEqual(self.actions, [])

    def test_expand_and_collapse_do_not_change_receipts_or_steal_focus(self):
        self.popup.present(self.message())
        before = dict(self.popup.notification)
        self.popup.expand_button.click()
        APP.processEvents()
        self.assertTrue(self.popup.expanded)
        self.assertTrue(self.popup.expand_button.isChecked())
        self.assertTrue(self.popup.body.isVisible())
        self.assertFalse(self.popup.preview.isVisible())
        self.assertFalse(self.popup.more_button.isVisible())
        self.assertTrue(self.popup.acknowledge_button.isVisible())
        self.assertGreater(self.popup.height(), 210)
        self.assertFalse(self.popup.timer.isActive())
        self.assertEqual(self.popup.notification, before)
        self.assertEqual(self.actions, [])
        self.assertTrue(self.popup.testAttribute(Qt.WA_ShowWithoutActivating))
        self.assertTrue(self.popup.windowFlags() & Qt.WindowDoesNotAcceptFocus)
        # Leaving a card while reading its expanded body must not hide it.
        QApplication.sendEvent(self.popup, QEvent(QEvent.Leave))
        self.assertFalse(self.popup.timer.isActive())
        self.popup.expand_button.click()
        self.assertFalse(self.popup.expanded)
        self.assertLessEqual(self.popup.height(), 210)
        self.assertTrue(self.popup.timer.isActive())
        self.assertEqual(self.actions, [])

    def test_next_message_resets_transient_expansion_to_saved_preference(self):
        self.popup.present(self.message())
        self.popup.expand_button.click()
        self.popup.present(self.message(22, body="A new personal message."))
        self.assertFalse(self.popup.expanded)
        self.assertEqual(self.popup.identifier, "22")
        self.assertEqual(self.popup.body.toPlainText(), "A new personal message.")
        self.settings["notification_size"] = "expanded"
        self.popup.present(self.message(23))
        self.assertTrue(self.popup.expanded)
        self.assertFalse(self.popup.timer.isActive())

    def test_live_setting_configures_existing_card_without_actions(self):
        self.popup.present(self.message())
        self.settings["notification_size"] = "expanded"
        self.popup.configure()
        self.assertTrue(self.popup.expanded)
        self.assertTrue(self.popup.isVisible())
        self.settings["notification_size"] = "compact"
        self.popup.configure()
        self.assertFalse(self.popup.expanded)
        self.assertTrue(self.popup.isVisible())
        self.assertTrue(self.popup.timer.isActive())
        self.assertEqual(self.actions, [])

    def test_more_menu_snooze_and_acknowledge_remain_explicit_and_recipient_specific(self):
        self.popup.present(self.message())
        actions = self.popup.more_menu.actions()
        self.assertEqual(self.actions, [])
        actions[1].trigger()
        self.assertEqual(self.actions, [("later", "21", 15)])
        self.assertFalse(self.popup.isVisible())
        self.popup.present(self.message(22))
        self.popup.acknowledge_action.trigger()
        self.assertEqual(self.actions[-1], ("ack", "22"))
        self.assertFalse(self.popup.isVisible())

    def test_logout_erases_preview_full_text_and_tooltips(self):
        self.popup.present(self.message(body="Private order information", sender_username="Private account"))
        self.popup.expand_button.click()
        self.popup.clear_session()
        self.assertEqual(self.popup.body.toPlainText(), "")
        self.assertEqual(self.popup.preview.text(), "")
        self.assertEqual(self.popup.title.text(), "")
        self.assertEqual(self.popup.title.toolTip(), "")
        self.assertEqual(self.popup.details.text(), "")
        self.assertEqual(self.popup.details.toolTip(), "")
        self.assertEqual(self.popup.notification, {})
        self.assertFalse(self.popup.isVisible())
        self.assertFalse(self.popup.timer.isActive())
        self.assertEqual(self.actions, [])

    def test_expansion_reclamps_to_small_monitor_with_negative_origin(self):
        available = QRect(-600, -300, 280, 320)
        screen = SimpleNamespace(availableGeometry=lambda: available)
        with patch("team_popup.QApplication.screenAt", return_value=screen):
            self.popup.present(self.message(), QPoint(-500, -200))
            self.assertTrue(available.contains(self.popup.geometry()))
            self.popup.expand_button.click()
            APP.processEvents()
            self.assertLessEqual(self.popup.width(), available.width())
            self.assertLessEqual(self.popup.height(), available.height())
            self.assertTrue(available.contains(self.popup.geometry()))
            self.popup.expand_button.click()
            self.assertTrue(available.contains(self.popup.geometry()))
        self.assertEqual(self.actions, [])

    def test_shared_folder_cards_use_compact_default_without_new_setting(self):
        settings = dict(DEFAULTS)
        settings.pop("notification_size", None)
        popup = TeamPopup(settings)
        try:
            self.assertTrue(popup.present({"id": "shared-note", "entity_kind": "note",
                "title": "A" * 1000, "author_name": "Ben" * 1000, "body": "Keep this note."}))
            APP.processEvents()
            self.assertLessEqual(popup.width(), 360)
            self.assertLessEqual(popup.height(), 210)
            self.assertEqual(popup.title.text(), "A" * 1000)
            popup.expand_button.click()
            self.assertEqual(popup.body.toPlainText(), "Keep this note.")
        finally:
            popup.shutdown()
            popup.deleteLater()

    def test_quiet_or_disabled_notifications_do_not_expose_stale_card(self):
        self.popup.present(self.message(body="Earlier private message"))
        self.settings["quiet_mode"] = True
        self.popup.configure()
        self.assertFalse(self.popup.isVisible())
        self.assertFalse(self.popup.present(self.message(22)))
        self.settings["quiet_mode"] = False
        self.settings["work_chat_notifications"] = False
        self.popup.configure()
        self.assertFalse(self.popup.present(self.message(23)))
        self.assertFalse(self.popup.isVisible())
        self.assertEqual(self.actions, [])


if __name__ == "__main__":
    unittest.main()
