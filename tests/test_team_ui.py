"""Employee messages stay explicit, recipient-specific, and safe during refresh."""
from copy import deepcopy
import unittest

from PySide6.QtCore import QObject, Signal, Qt, QCoreApplication, QEvent, QDateTime
from PySide6.QtWidgets import QApplication

from config import DEFAULTS
from team_popup import TeamPopup
from team_window import TeamWindow

app = QApplication.instance() or QApplication([])


class CachedService(QObject):
    changed = Signal()
    error = Signal(str)
    status_changed = Signal(str)
    message_queued = Signal(object)

    def __init__(self):
        super().__init__()
        self.enabled = True
        self.status = "Connected"
        self.people = [{"id": "ada", "name": "Ada"}, {"id": "ben", "name": "Ben"}]
        self.rows = [{"id": "message-1", "title": "Call the customer", "body": "Pickup at 3 PM.",
            "author_name": "Ben", "customer": "Acme", "entity_kind": "message", "note_id": "order-1",
            "read": False, "acknowledged": False, "next_due": 2000000000}]
        self.sent = []
        self.actions = []
        self.failure = None
        self.accepted = True

    def identity(self):
        return {"id": "ada", "name": "Ada"}

    def roster(self):
        return deepcopy(self.people)

    def inbox(self):
        return deepcopy(self.rows)

    def send_message(self, title, body, recipients, **options):
        if self.failure:
            raise self.failure
        if not self.accepted:
            self.error.emit("Connect to the shared work folder first.")
            return False
        self.sent.append((title, body, recipients, options))
        return True

    def acknowledge(self, identifier, entity_kind=None):
        self.actions.append(("acknowledge", identifier, entity_kind))

    def snooze(self, identifier, minutes, entity_kind=None):
        self.actions.append(("snooze", identifier, minutes, entity_kind))

    def mark_read(self, identifier, entity_kind=None):
        self.actions.append(("read", identifier, entity_kind))
        for row in self.rows:
            if row["id"] == identifier:
                row["read"] = True

    def pump(self):
        self.actions.append(("refresh",))


class TeamUiTests(unittest.TestCase):
    def setUp(self):
        self.settings = dict(DEFAULTS, team_sync_enabled=True, team_shared_folder=r"\\server\team",
            team_employee_id="ada", team_employee_name="Ada", team_reminder_minutes=30,
            team_share_notes_with_ai=False)
        self.service = CachedService()
        self.window = TeamWindow(self.service, self.settings)
        self.popup = TeamPopup(self.settings)

    def tearDown(self):
        self.popup.shutdown()
        self.popup.close()
        self.window.close()
        self.popup.deleteLater()
        self.window.deleteLater()
        self.service.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def select_recipient(self, identifier):
        for index in range(self.window.recipients.count()):
            item = self.window.recipients.item(index)
            if item.data(Qt.UserRole) == identifier:
                item.setCheckState(Qt.Checked)

    def draft(self):
        self.window.title.setText("Customer pickup")
        self.window.body.setPlainText("Call before pickup.")
        self.window.customer.setText("Acme")
        self.select_recipient("ben")

    def test_connect_selects_existing_employee_and_emits_preferences_only(self):
        applied = []
        self.window.preferences_requested.connect(applied.append)
        self.window.employee_choice.setCurrentIndex(2)
        self.window.shared_folder.setText(r"\\office\jeffery")
        self.window.connect_button.click()
        self.assertEqual(applied[0]["team_employee_id"], "ben")
        self.assertEqual(applied[0]["team_employee_name"], "Ben")
        self.assertEqual(applied[0]["team_shared_folder"], r"\\office\jeffery")
        self.assertFalse(applied[0]["team_share_notes_with_ai"])
        self.assertEqual(self.service.sent, [])
        self.window.disconnect_button.click()
        self.assertEqual(applied[-1], {"team_sync_enabled": False})

    def test_new_employee_selection_and_setup_drafts_survive_roster_refresh(self):
        self.window.employee_choice.setCurrentIndex(0)
        self.window.employee_name.setText("New colleague")
        self.window.shared_folder.setText(r"\\new\folder")
        self.service.people.append({"id": "cam", "name": "Cam"})
        self.service.changed.emit()
        self.assertEqual(self.window.employee_choice.currentIndex(), 0)
        self.assertEqual(self.window.employee_name.text(), "New colleague")
        self.assertEqual(self.window.shared_folder.text(), r"\\new\folder")
        applied = []
        self.window.preferences_requested.connect(applied.append)
        self.window.connect_team()
        self.assertEqual(applied[0]["team_employee_id"], "")

    def test_setup_requires_folder_and_display_name(self):
        applied = []
        self.window.preferences_requested.connect(applied.append)
        self.window.employee_name.clear()
        self.window.connect_team()
        self.assertEqual(applied, [])
        self.assertIn("display name", self.window.status.text())

    def test_copied_settings_never_preselect_another_employee(self):
        self.settings["team_employee_id"] = "ben"
        self.settings["team_employee_name"] = "Ben"
        other = TeamWindow(self.service, self.settings)
        try:
            self.assertEqual(other.employee_choice.currentData()["id"], "ada")
            self.assertEqual(other.employee_name.text(), "Ada")
            applied = []
            other.preferences_requested.connect(applied.append)
            other.connect_button.click()
            self.assertEqual(applied[0]["team_employee_id"], "ada")
        finally:
            other.close()
            other.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_copied_settings_without_local_identity_leave_new_profile_selected(self):
        self.settings["team_employee_id"] = "ben"
        original_identity = self.service.identity
        self.service.identity = lambda: {}
        other = TeamWindow(self.service, self.settings)
        try:
            self.assertEqual(other.employee_choice.currentIndex(), 0)
            self.assertEqual(other.employee_choice.currentData(), {})
        finally:
            self.service.identity = original_identity
            other.close()
            other.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_send_is_explicit_and_uses_stable_recipient_ids_and_schedule(self):
        self.draft()
        self.window.scheduled.setChecked(True)
        scheduled = QDateTime.fromSecsSinceEpoch(2000000000)
        self.window.due.setDateTime(scheduled)
        self.window.repeat.setValue(15)
        self.assertEqual(self.service.sent, [])
        self.window.send_button.click()
        title, body, recipients, options = self.service.sent[0]
        self.assertEqual((title, body, recipients), ("Customer pickup", "Call before pickup.", ["ben"]))
        self.assertEqual(options["first_due"], 2000000000)
        self.assertEqual(options["repeat_minutes"], 15)
        self.assertEqual(options["customer"], "Acme")
        self.assertEqual(options["note_id"], "")
        self.assertEqual(self.window.body.toPlainText(), "Call before pickup.")
        self.assertFalse(self.window.send_button.isEnabled())
        self.window.send_button.click()
        self.assertEqual(len(self.service.sent), 1)

    def test_everyone_and_acknowledgment_only_message(self):
        self.draft()
        self.window.everyone.setChecked(True)
        self.window.repeat.setValue(0)
        self.window.send_button.click()
        self.assertEqual(self.service.sent[0][2], [])
        self.assertIsNone(self.service.sent[0][3]["first_due"])
        self.assertEqual(self.service.sent[0][3]["repeat_minutes"], 0)
        self.assertFalse(self.window.recipients.isEnabled())

    def test_invalid_or_failed_sends_preserve_draft(self):
        self.window.title.setText("A subject")
        self.window.body.setPlainText("Keep this draft")
        self.window.send_button.click()
        self.assertEqual(self.service.sent, [])
        self.select_recipient("ben")
        self.window.body.setPlainText("x" * 10001)
        self.window.send_button.click()
        self.assertIn("10,000", self.window.status.text())
        self.assertEqual(self.service.sent, [])
        self.window.body.setPlainText("Keep this draft")
        self.service.failure = OSError("Disk is unavailable")
        self.window.send_button.click()
        self.assertEqual(self.window.body.toPlainText(), "Keep this draft")
        self.assertIn("Disk is unavailable", self.window.status.text())
        self.assertTrue(self.window.send_button.isEnabled())

    def test_async_error_and_refresh_keep_composer_selection_and_content(self):
        self.draft()
        self.window.send_button.click()
        self.service.changed.emit()
        self.assertEqual(self.window.title.text(), "Customer pickup")
        self.assertEqual(self.window.body.toPlainText(), "Call before pickup.")
        checked = [self.window.recipients.item(i).data(Qt.UserRole) for i in range(self.window.recipients.count())
            if self.window.recipients.item(i).checkState() == Qt.Checked]
        self.assertEqual(checked, ["ben"])
        self.assertFalse(self.window.send_button.isEnabled())
        self.service.error.emit("Local outbox could not be saved")
        self.assertTrue(self.window.send_button.isEnabled())
        self.assertIn("outbox", self.window.status.text())

    def queued_event(self):
        title, body, recipients, options = self.service.sent[-1]
        return {"payload": {"title": title, "body": body, "recipients": recipients,
            "customer": options["customer"], "repeat_minutes": options["repeat_minutes"],
            "due_at": options["first_due"]}}

    def test_draft_clears_after_durable_outbox_commit_only(self):
        self.draft()
        self.window.send_button.click()
        self.assertEqual(self.window.body.toPlainText(), "Call before pickup.")
        self.service.message_queued.emit(self.queued_event())
        self.assertEqual(self.window.body.toPlainText(), "")
        self.assertEqual(self.window.title.text(), "")
        self.assertIn("saved to your local outbox", self.window.status.text())

    def test_durable_commit_does_not_erase_a_newer_message_draft(self):
        self.draft()
        self.window.send_button.click()
        self.window.body.setPlainText("A new message I am still writing.")
        self.service.message_queued.emit(self.queued_event())
        self.assertEqual(self.window.body.toPlainText(), "A new message I am still writing.")
        self.assertTrue(self.window.send_button.isEnabled())

    def test_message_commit_during_customer_assignment_does_not_resurrect_sent_draft(self):
        self.draft()
        self.window.send_button.click()
        self.window.compose_note({"id": "order-1", "title": "Acme order", "body": "Pack the items."})
        self.service.message_queued.emit(self.queued_event())
        self.assertEqual(self.window.body.toPlainText(), "Pack the items.")
        self.window.clear_source_button.click()
        self.assertEqual(self.window.body.toPlainText(), "")
        self.assertEqual(self.window.title.text(), "")

    def test_rejected_queue_request_keeps_error_and_draft(self):
        self.draft()
        self.service.accepted = False
        self.window.send_button.click()
        self.assertEqual(self.service.sent, [])
        self.assertEqual(self.window.pending_messages, [])
        self.assertEqual(self.window.body.toPlainText(), "Call before pickup.")
        self.assertIn("Connect to", self.window.status.text())
        self.assertTrue(self.window.send_button.isEnabled())

    def test_customer_assignment_keeps_note_and_message_schedules_separate(self):
        self.draft()
        original_due = self.window.due.dateTime()
        assignments = []
        self.window.assignment_requested.connect(lambda identifier, people: assignments.append((identifier, people)))
        note = {"id": "order-1", "title": "Acme pickup", "body": "Pack the order.", "customer": "Acme", "team_recipients": ["ben"]}
        self.window.compose_note(note)
        self.assertTrue(self.window.body.isReadOnly())
        self.assertFalse(self.window.repeat.isEnabled())
        self.window.send_button.click()
        self.assertEqual(assignments, [("order-1", ["ben"])])
        self.assertEqual(self.service.sent, [])
        self.window.clear_source_button.click()
        self.assertEqual(self.window.title.text(), "Customer pickup")
        self.assertEqual(self.window.body.toPlainText(), "Call before pickup.")
        self.assertEqual(self.window.due.dateTime(), original_due)
        self.assertFalse(self.window.body.isReadOnly())

    def test_failed_customer_assignment_does_not_replace_error_with_success(self):
        self.window.compose_note({"id": "order-1", "title": "Acme order", "body": "Keep the order."})
        self.window.assignment_requested.connect(lambda identifier, people: self.service.error.emit("The note could not be saved."))
        self.window.send_button.click()
        self.assertEqual(self.window.status.text(), "The note could not be saved.")
        self.assertEqual(self.window.body.toPlainText(), "Keep the order.")
        self.assertTrue(self.window.send_button.isEnabled())

    def test_inbox_actions_use_employee_delivery_not_source_note_completion(self):
        source = []
        self.window.source_requested.connect(source.append)
        self.window.messages.setCurrentRow(0)
        self.window.acknowledge_button.click()
        self.window.snooze_minutes.setCurrentIndex(2)
        self.window.snooze_button.click()
        self.window.open_source_button.click()
        self.assertIn(("acknowledge", "message-1", "message"), self.service.actions)
        self.assertIn(("snooze", "message-1", 30, "message"), self.service.actions)
        self.assertEqual(source, ["order-1"])
        self.assertFalse(any(action[0] == "complete" for action in self.service.actions))

    def test_opening_inbox_marks_read_but_background_refresh_does_not(self):
        self.assertEqual(self.service.actions, [])
        self.window.show()
        self.assertTrue(self.window.open_message("message-1", "message"))
        self.assertIn(("read", "message-1", "message"), self.service.actions)
        self.assertEqual(self.window.message_title.text(), "Call the customer")
        self.assertFalse(self.window.open_message("removed"))

    def notification(self, **updates):
        return dict({"id": "message-1", "title": "<b>Literal title</b>",
            "body": "<script>Keep this literal</script>", "author_name": "<b>Ben</b>",
            "customer": "Acme", "entity_kind": "message", "notification_kind": "added"}, **updates)

    def test_popup_is_literal_passive_and_distinguishes_reminders(self):
        self.assertTrue(self.popup.present(self.notification()))
        self.assertTrue(self.popup.testAttribute(Qt.WA_ShowWithoutActivating))
        self.assertEqual(self.popup.title.textFormat(), Qt.PlainText)
        self.assertEqual(self.popup.details.textFormat(), Qt.PlainText)
        self.assertEqual(self.popup.body.toPlainText(), "<script>Keep this literal</script>")
        self.assertEqual(self.popup.heading.text(), "New team message")
        self.assertEqual(self.popup.notification["entity_kind"], "message")
        self.popup.present(self.notification(notification_kind="reminder"))
        self.assertEqual(self.popup.heading.text(), "Team message reminder")

    def test_popup_dismiss_never_acknowledges_and_actions_are_recipient_specific(self):
        acknowledgments, snoozes, opened = [], [], []
        self.popup.acknowledge_requested.connect(acknowledgments.append)
        self.popup.snooze_requested.connect(lambda identifier, minutes: snoozes.append((identifier, minutes)))
        self.popup.open_requested.connect(opened.append)
        self.popup.present(self.notification())
        self.popup.dismiss_button.click()
        self.assertEqual(acknowledgments, [])
        self.assertFalse(self.popup.isVisible())
        self.popup.present(self.notification())
        self.popup.snooze_minutes.setCurrentIndex(1)
        self.popup.snooze_button.click()
        self.assertEqual(snoozes, [("message-1", 15)])
        self.popup.present(self.notification())
        self.popup.open_button.click()
        self.assertEqual(opened, ["message-1"])
        self.assertEqual(acknowledgments, [])
        self.popup.present(self.notification())
        self.popup.acknowledge_button.click()
        self.assertEqual(acknowledgments, ["message-1"])

    def test_popup_quiet_mode_and_remote_completion_hide_without_actions(self):
        acknowledged = []
        self.popup.acknowledge_requested.connect(acknowledged.append)
        self.settings["quiet_mode"] = True
        self.assertFalse(self.popup.present(self.notification()))
        self.assertFalse(self.popup.isVisible())
        self.settings["quiet_mode"] = False
        self.popup.present(self.notification(entity_kind="note", notification_kind="reminder"))
        self.assertEqual(self.popup.heading.text(), "Shared note reminder")
        self.popup.dismiss_if_inactive(set())
        self.assertFalse(self.popup.isVisible())
        self.assertFalse(self.popup.timer.isActive())
        self.assertEqual(acknowledged, [])


if __name__ == "__main__":
    unittest.main()
