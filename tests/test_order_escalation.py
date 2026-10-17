"""Personal reminders use complete shared snapshots and never alter orders."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QEvent, Qt, Signal
from PySide6.QtWidgets import QApplication

from config import DEFAULTS
from order_escalation import OrderEscalationService
from overdue_orders_window import OverdueOrderPopup, OverdueOrdersWindow

APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)
NOW = 1_800_000_000.0
FIRST, SECOND = "a" * 32, "b" * 32


def order(identifier=FIRST, due=NOW, **fields):
    return {"id": identifier, "revision": 1, "deleted": False,
            "entry": {"id": identifier, "kind": "order", "order_type": "order",
                      "done": False, "order_status": "new", "order_due": due,
                      "customer": "Private customer", "order_ref": "Private order",
                      "title": "Customer brake pads", **fields}}


class BusinessService(QObject):
    changed = Signal(str)


class SharedBusiness(QObject):
    status_changed = Signal(str, bool)

    def __init__(self):
        super().__init__()
        self.service = BusinessService(self)
        self.api = SimpleNamespace(base_url="http://127.0.0.1:8765")
        self.token, self.user = "secret-token", {"id": 1, "username": "alice"}
        self.ready = False
        self._records = {}
        self.refresh_count = 0

    def refresh(self):
        self.refresh_count += 1


class EscalationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "reminders.json"
        self.settings = dict(DEFAULTS, quiet_mode=False, note_reminders=True)
        self.shared = SharedBusiness()
        self.now, self.available = NOW, True
        self.service = self.make_service()
        self.offers, self.errors, self.resets = [], [], []
        self.service.notification.connect(self.offers.append)
        self.service.error.connect(self.errors.append)
        self.service.reset.connect(lambda: self.resets.append(True))
        self.widgets = []

    def make_service(self):
        return OrderEscalationService(self.settings, self.shared, self.path,
                                      can_notify=lambda: self.available, clock=lambda: self.now)

    def tearDown(self):
        self.service.stop()
        for widget in self.widgets:
            widget.hide()
            widget.deleteLater()
        self.service.deleteLater()
        self.shared.deleteLater()
        APP.sendPostedEvents(None, QEvent.DeferredDelete)
        self.temp.cleanup()

    def attach(self, records=None, ready=True):
        self.shared._records = {row["id"]: row for row in (records if records is not None else [order()])}
        self.shared.ready = ready
        self.service.set_session(self.shared.api.base_url, self.shared.token, self.shared.user)

    def present(self):
        payload = self.offers[-1]
        self.assertTrue(self.service.mark_notification_presented(payload))
        return payload

    def dismiss(self, payload):
        self.service.release_notification(payload)
        APP.processEvents()

    def popup(self):
        popup = OverdueOrderPopup(self.settings)
        self.widgets.append(popup)
        return popup

    def test_initial_partial_snapshot_never_shows_customer_details(self):
        self.attach(ready=False)
        self.assertEqual(self.offers, [])
        self.assertEqual(self.service.rows, [])
        self.assertFalse(self.service._established)
        self.shared.ready = True
        self.shared.service.changed.emit("")
        self.assertEqual(len(self.offers), 1)
        self.assertEqual(self.offers[0]["id"], FIRST)

    def test_each_tier_is_presented_once_without_daily_repeat(self):
        self.attach()
        for tier, elapsed in ((0, 0), (1, 3600), (2, 86400)):
            self.now = NOW + elapsed
            self.service.pump()
            self.assertEqual(self.offers[-1]["tier"], tier)
            payload = self.present()
            self.dismiss(payload)
            count = len(self.offers)
            self.service.pump()
            self.assertEqual(len(self.offers), count)
        self.now += 4 * 86400
        self.service.pump()
        self.assertEqual(len(self.offers), 3)

    def test_first_visit_to_old_order_shows_only_current_tier(self):
        self.attach([order(due=NOW - 172800)])
        self.assertEqual([row["tier"] for row in self.offers], [2])
        self.dismiss(self.present())
        self.service.pump()
        self.assertEqual(len(self.offers), 1)

    def test_only_one_card_can_be_offered_or_displayed(self):
        self.attach([order(), order(SECOND)])
        self.assertEqual(len(self.offers), 1)
        self.service.pump()
        self.assertEqual(len(self.offers), 1)
        first = self.present()
        self.service.pump()
        self.assertEqual(len(self.offers), 1)
        self.service.release_notification(first)
        self.assertEqual(len(self.offers), 1)
        APP.processEvents()
        self.assertEqual([row["id"] for row in self.offers], [FIRST, SECOND])

    def test_refused_card_retries_without_consuming_tier_or_busy_loop(self):
        self.attach()
        self.service.release_notification(self.offers[-1])
        APP.processEvents()
        self.assertEqual(len(self.offers), 1)
        self.assertFalse(self.path.exists())
        self.service.pump()
        self.assertEqual(len(self.offers), 2)
        self.present()

    def test_quiet_fullscreen_busy_and_disabled_reminders_defer(self):
        self.available = False
        self.attach()
        self.assertEqual(self.offers, [])
        self.available = True
        self.settings["quiet_mode"] = True
        self.service.pump()
        self.assertEqual(self.offers, [])
        self.settings.update(quiet_mode=False, note_reminders=False)
        self.service.pump()
        self.assertEqual(self.offers, [])
        self.settings["note_reminders"] = True
        self.service.pump()
        self.assertEqual(len(self.offers), 1)
        self.assertFalse(self.path.exists())

    def test_quiet_transition_clears_offer_without_marking_presented(self):
        self.attach()
        payload = self.offers[-1]
        self.settings["quiet_mode"] = True
        self.service.pump()
        self.assertFalse(self.service.mark_notification_presented(payload))
        self.assertFalse(self.path.exists())
        self.settings["quiet_mode"] = False
        self.service.pump()
        self.assertEqual(len(self.offers), 2)

    def test_unrelated_revision_does_not_rearm_but_due_change_does(self):
        self.attach()
        self.dismiss(self.present())
        row = self.shared._records[FIRST]
        row["revision"] = 2
        row["entry"]["body"] = "Changed comment"
        row["entry"]["customer"] = "Updated customer"
        self.service.pump()
        self.assertEqual(len(self.offers), 1)
        row["entry"]["order_due"] = NOW + 60
        self.service.pump()
        self.assertEqual(self.service.rows, [])
        self.now += 60
        self.service.pump()
        self.assertEqual(len(self.offers), 2)
        self.assertEqual(self.offers[-1]["customer"], "Updated customer")

    def test_done_quotes_deleted_future_and_invalid_orders_are_excluded(self):
        records = [order(f"{index:032x}", **fields) for index, fields in enumerate((
            {"done": True}, {"order_type": "quote"}, {"order_status": "delivered"},
            {"order_status": "cancelled"}, {"kind": "list"}, {"order_due": NOW + 1},
            {"order_due": float("nan")}, {"order_due": 0}, {"order_due": True}), 1)]
        deleted = order("e" * 32)
        deleted["deleted"] = True
        self.attach(records + [deleted, order(order_status="ready")])
        self.assertEqual([row["id"] for row in self.service.rows], [FIRST])
        self.assertEqual(len(self.offers), 1)

    def test_completion_invalidates_display_and_advances_without_order_writes(self):
        records = [order(), order(SECOND)]
        self.attach(records)
        original = deepcopy(self.shared._records)
        self.present()
        self.service.pump()
        self.assertEqual(self.shared._records, original)
        self.shared._records[FIRST]["entry"]["done"] = True
        self.service.pump()
        self.assertIsNone(self.service._active)
        self.assertEqual(self.offers[-1]["id"], SECOND)
        self.assertNotIn(FIRST, self.service._saved[self.service._scope])

    def test_offline_uses_only_last_complete_snapshot_and_labels_it(self):
        self.attach()
        self.dismiss(self.present())
        self.shared.ready = False
        self.shared._records[FIRST]["entry"]["customer"] = "Partial update customer"
        self.shared._records[FIRST]["entry"]["order_due"] = NOW + 100
        self.shared._records[SECOND] = order(SECOND)
        self.service.pump()
        self.assertEqual(len(self.service.rows), 1)
        self.assertEqual(self.service.rows[0]["customer"], "Private customer")
        self.assertTrue(self.service.rows[0]["cached"])
        self.assertIn("Offline", self.service.status)
        self.shared.ready = True
        self.service.pump()
        self.assertEqual([row["id"] for row in self.service.rows], [SECOND])
        self.assertFalse(self.service.rows[0]["cached"])

    def test_account_switch_partial_snapshot_cannot_expose_previous_account(self):
        self.attach()
        old = self.offers[-1]
        self.shared.user = {"id": 2, "username": "bob"}
        self.shared.ready = False
        self.service.set_session(self.shared.api.base_url, self.shared.token, self.shared.user)
        self.assertEqual(self.service.rows, [])
        self.assertEqual(self.service._snapshot, [])
        self.assertFalse(self.service.mark_notification_presented(old))
        self.assertFalse(self.path.exists())

    def test_mismatched_server_or_token_clears_live_and_cached_details(self):
        self.attach()
        self.present()
        self.shared.token = "other-session"
        self.shared.ready = False
        self.service.pump()
        self.assertEqual(self.service.rows, [])
        self.assertEqual(self.service._snapshot, [])
        self.assertIsNone(self.service._active)
        self.assertFalse(self.service._established)

    def test_snooze_survives_restart_and_repeats_once_after_expiry(self):
        self.attach()
        self.dismiss(self.present())
        self.assertTrue(self.service.snooze(FIRST, 15))
        self.service.stop()
        self.service.deleteLater()
        self.service = self.make_service()
        self.service.notification.connect(self.offers.append)
        self.service.set_session(self.shared.api.base_url, self.shared.token, self.shared.user)
        self.assertEqual(len(self.offers), 1)
        self.now += 899
        self.service.pump()
        self.assertEqual(len(self.offers), 1)
        self.now += 1
        self.service.pump()
        self.assertEqual(len(self.offers), 2)
        self.dismiss(self.present())
        self.service.pump()
        self.assertEqual(len(self.offers), 2)

    def test_snooze_uses_latest_tier_and_rejects_invalid_actions(self):
        self.attach()
        self.dismiss(self.present())
        self.assertTrue(self.service.snooze(FIRST, 1440))
        for value in (0, -1, 10, True, 15.0, "15"):
            self.assertFalse(self.service.snooze(FIRST, value))
        self.assertFalse(self.service.snooze(SECOND, 15))
        self.now += 86400
        self.service.pump()
        self.assertEqual(self.offers[-1]["tier"], 2)

    def test_journal_contains_no_customer_text_credentials_or_server_names(self):
        self.attach()
        self.present()
        self.service.snooze(FIRST, 60)
        contents = self.path.read_text()
        for secret in ("Private", "customer", "alice", "127.0.0.1", "secret-token", "brake"):
            self.assertNotIn(secret, contents)
        data = json.loads(contents)
        self.assertEqual(data["version"], 1)
        self.assertEqual(set(next(iter(data["accounts"].values()))[FIRST]),
                         {"due", "tier", "snooze_until", "delivered_at"})

    def test_server_and_account_journals_are_isolated(self):
        self.attach()
        self.dismiss(self.present())
        self.shared.user = {"id": 2, "username": "bob"}
        self.service.set_session(self.shared.api.base_url, self.shared.token, self.shared.user)
        self.assertEqual(len(self.offers), 2)
        self.dismiss(self.present())
        self.shared.api.base_url = "http://192.168.0.4:8765"
        self.service.set_session(self.shared.api.base_url, self.shared.token, self.shared.user)
        self.assertEqual(len(self.offers), 3)
        self.dismiss(self.present())
        self.shared.api.base_url = "http://127.0.0.1:8765"
        self.shared.user = {"id": 1, "username": "alice"}
        self.service.set_session(self.shared.api.base_url, self.shared.token, self.shared.user)
        self.assertEqual(len(self.offers), 3)

    def test_corrupt_journal_is_ignored_and_save_failure_is_reported_once(self):
        self.path.write_text('{"version":1,"accounts":{"bad-scope":{}}}')
        fresh = self.make_service()
        self.assertEqual(fresh._saved, {})
        fresh.deleteLater()
        self.attach()
        with patch("order_escalation.tempfile.NamedTemporaryFile", side_effect=OSError("disk full")):
            self.present()
            self.service.snooze(FIRST, 60)
        self.assertEqual(len(self.errors), 1)
        self.assertIn("save", self.errors[0])
        self.assertFalse(list(self.path.parent.glob(".order-escalations-*")))

    def test_logout_and_stop_clear_private_rows_and_cancel_queued_timer(self):
        self.attach([order(), order(SECOND)])
        self.service.release_notification(self.present())
        self.assertTrue(self.service._next_timer.isActive())
        self.service.stop()
        APP.processEvents()
        self.assertEqual(len(self.offers), 1)
        self.assertEqual(self.service.rows, [])
        self.assertEqual(self.service._snapshot, [])
        self.assertFalse(self.service.timer.isActive())
        self.assertFalse(self.service._next_timer.isActive())

    def test_order_board_has_native_controls_and_actions(self):
        self.attach()
        board = OverdueOrdersWindow(self.service, self.settings)
        self.widgets.append(board)
        opened = []
        board.open_requested.connect(opened.append)
        for flag in (Qt.WindowMinimizeButtonHint, Qt.WindowMaximizeButtonHint, Qt.WindowCloseButtonHint):
            self.assertTrue(board.windowFlags() & flag)
        self.assertEqual(board.table.rowCount(), 1)
        self.assertFalse(board.open_button.isEnabled())
        board.table.selectRow(0)
        board.open_button.click()
        self.assertEqual(opened, [FIRST])
        board.snooze_selected(60)
        self.assertIn("Snoozed", board.table.item(0, 5).text())
        board.refresh_button.click()
        self.assertEqual(self.shared.refresh_count, 1)
        self.service.set_session("", "", None)
        self.assertEqual(board.table.rowCount(), 0)
        self.assertFalse(board.open_button.isEnabled())
        self.assertFalse(board.refresh_button.isEnabled())

    def test_popup_is_compact_plain_text_passive_and_clears_private_content(self):
        self.attach([order(customer="<b>Customer</b>")])
        popup = self.popup()
        dismissed = []
        popup.dismissed.connect(dismissed.append)
        self.assertTrue(popup.display(self.offers[-1]))
        self.assertLessEqual(popup.width(), 350)
        self.assertLessEqual(popup.height(), 178)
        self.assertTrue(popup.windowFlags() & Qt.WindowDoesNotAcceptFocus)
        self.assertTrue(popup.testAttribute(Qt.WA_ShowWithoutActivating))
        self.assertEqual(popup.subject.textFormat(), Qt.PlainText)
        self.assertIn("<b>Customer</b>", popup.subject.text())
        self.assertFalse(popup.display(self.offers[-1]))
        popup.clear()
        popup.clear()
        self.assertEqual(len(dismissed), 1)
        self.assertEqual(popup.notification, {})
        self.assertEqual(popup.subject.text(), "")
        self.assertEqual(popup.details.text(), "")
        self.assertFalse(popup.timer.isActive())

    def test_popup_timeout_dismissal_advances_next_order(self):
        self.attach([order(), order(SECOND)])
        popup = self.popup()
        popup.dismissed.connect(self.service.release_notification)
        self.service.reset.connect(popup.clear)
        self.assertTrue(popup.display(self.offers[-1]))
        self.present()
        popup.timer.timeout.emit()
        self.assertFalse(popup.isVisible())
        APP.processEvents()
        self.assertEqual(self.offers[-1]["id"], SECOND)

    def test_popup_snooze_runs_before_next_card_and_open_emits_exact_id(self):
        self.attach([order(), order(SECOND)])
        popup = self.popup()
        popup.dismissed.connect(self.service.release_notification)
        popup.snooze_requested.connect(self.service.snooze)
        opened = []
        popup.open_requested.connect(opened.append)
        self.assertTrue(popup.display(self.offers[-1]))
        self.present()
        popup.snooze_for(15)
        APP.processEvents()
        self.assertEqual(self.offers[-1]["id"], SECOND)
        first = next(row for row in self.service.rows if row["id"] == FIRST)
        self.assertEqual(first["snooze_until"], NOW + 900)
        self.assertTrue(popup.display(self.offers[-1]))
        self.present()
        popup.open_order()
        self.assertEqual(opened, [SECOND])
        self.assertFalse(popup.isVisible())

    def test_popup_setting_changes_preserve_visible_card_and_timeout(self):
        self.attach()
        popup = self.popup()
        self.assertTrue(popup.display(self.offers[-1]))
        self.settings["always_on_top"] = not self.settings["always_on_top"]
        popup.configure()
        self.assertTrue(popup.isVisible())
        self.assertTrue(popup.timer.isActive())
        self.assertEqual(popup.identifier, FIRST)
        self.settings["quiet_mode"] = True
        popup.configure()
        self.assertFalse(popup.isVisible())
        self.assertFalse(popup.timer.isActive())
        self.assertEqual(popup.subject.text(), "")

    def test_popup_quiet_mode_refuses_display_without_consuming_offer(self):
        self.attach()
        popup = self.popup()
        self.settings["quiet_mode"] = True
        self.assertFalse(popup.display(self.offers[-1]))
        self.assertFalse(self.service.mark_notification_presented(self.offers[-1]))
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
