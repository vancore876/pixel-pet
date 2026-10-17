"""Backup controls require IT authentication and clear state across sessions."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_it_window as base


def backup_status(running=False):
    return {"enabled": True, "running": running, "keep_days": 14,
            "last_success_at": "2026-10-16T10:00:00Z", "last_error": None,
            "next_due": "2026-10-17T00:00:00Z", "directory": r"C:\Private\office-backups",
            "backups": [{"name": "office-20261016-100000.sqlite", "created_at": "2026-10-16T10:00:00Z",
                         "size": 204800, "verified": True}]}


class ITBackupWindowTests(unittest.TestCase):
    setUp = base.ITWindowTests.setUp
    tearDown = base.ITWindowTests.tearDown
    login = base.ITWindowTests.login
    dashboard = base.ITWindowTests.dashboard

    def ready(self, running=False):
        self.dashboard()
        self.api.finish(self.api.pending("/api/it/backups"), backup_status(running))

    def test_bootstrap_cannot_load_backups_and_normal_dashboard_shows_verified_metadata(self):
        self.login(True)
        self.assertNotIn("/api/it/backups", [call["path"] for call in self.api.calls])
        self.assertFalse(self.window.backup_now_button.isEnabled())
        self.window._lock()
        self.ready()
        self.assertEqual(self.window.backups_table.rowCount(), 1)
        self.assertEqual(self.window.backups_table.item(0, 3).text(), "Yes")
        self.assertIn("200.0 KiB", self.window.backups_table.item(0, 2).text())
        self.assertTrue(self.window.backup_now_button.isEnabled())
        self.assertEqual(self.settings.values, self.original_settings)

    def test_manual_backup_requires_explicit_password_and_does_not_persist_it(self):
        self.ready()
        self.window.backup_now_button.click()
        dialog = self.window._backup_dialog
        dialog.submit_button.click()
        self.assertIn("password", dialog.status.text())
        self.assertFalse(any(call["method"] == "POST" and call["path"] == "/api/it/backups"
                             for call in self.api.calls))
        dialog.admin_password.setText("private backup password")
        dialog.submit_button.click()
        call = self.api.pending("/api/it/backups")
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["token"], self.window.token)
        self.assertEqual(call["body"], {"admin_password": "private backup password"})
        self.assertFalse(dialog.admin_password.text())
        self.api.finish(call, {"status": "queued"}, status=202)
        self.assertFalse(dialog.isVisible())
        self.api.finish(self.api.pending("/api/it/backups"), backup_status(True))
        self.assertTrue(self.window._backup_poll.isActive())
        self.assertFalse(self.window.backup_now_button.isEnabled())
        self.assertEqual(self.settings.values, self.original_settings)

    def test_polling_stops_when_hidden_completed_or_locked(self):
        self.ready(True)
        self.assertTrue(self.window._backup_poll.isActive())
        self.window.hide()
        self.assertFalse(self.window._backup_poll.isActive())
        self.window.show()
        self.api.finish(self.api.pending("/api/it/backups"), backup_status(False))
        self.assertFalse(self.window._backup_poll.isActive())
        self.window._lock()
        self.assertEqual(self.window.backups_table.rowCount(), 0)
        self.assertFalse(self.window.backup_directory.text())
        self.assertFalse(self.window._backup_poll.isActive())

    def test_logout_clears_password_dialog_and_rejects_late_backup_data(self):
        self.ready()
        self.window.backup_now_button.click()
        dialog = self.window._backup_dialog
        dialog.admin_password.setText("unsent private password")
        self.window.refresh_backups()
        call = self.api.pending("/api/it/backups")
        self.window.close()
        self.assertFalse(dialog.admin_password.text())
        self.assertFalse(dialog.isVisible())
        self.api.finish(call, backup_status(True))
        self.assertEqual(self.window.backups_table.rowCount(), 0)
        self.assertFalse(self.window._backup_poll.isActive())
        self.assertFalse(self.window.token)

    def test_missing_endpoint_keeps_other_it_tools_available(self):
        self.dashboard()
        self.api.finish(self.api.pending("/api/it/backups"), error="Not Found", status=404)
        self.assertIn("Update and restart", self.window.backup_status.text())
        self.assertTrue(self.window.token)
        self.assertTrue(self.window.refresh_button.isEnabled())
        self.assertFalse(self.window.backup_now_button.isEnabled())

    def test_malformed_or_traversing_backup_names_never_appear(self):
        self.ready()
        self.window.refresh_backups()
        bad = backup_status()
        bad["backups"][0]["name"] = "../private.sqlite"
        self.api.finish(self.api.pending("/api/it/backups"), bad)
        self.assertEqual(self.window.backups_table.rowCount(), 0)
        self.assertIn("invalid backup", self.window.backup_status.text())
        self.assertFalse(self.window.backup_now_button.isEnabled())

    def test_forced_refresh_ignores_older_response_and_restore_never_sends_a_write(self):
        self.ready()
        self.window.refresh_backups()
        older = self.api.pending("/api/it/backups")
        self.window.refresh_backups(force=True)
        newer = self.api.pending("/api/it/backups")
        self.api.finish(newer, backup_status(False))
        self.api.finish(older, backup_status(True))
        self.assertFalse(self.window._backups_running)
        self.assertFalse(self.window._backup_poll.isActive())
        count = len(self.api.calls)
        self.window.backup_restore_button.click()
        self.assertEqual(len(self.api.calls), count)
        self.assertFalse(self.window.backup_restore_help.isHidden())
        self.assertIn("stop the office server", self.window.backup_restore_help.text())


if __name__ == "__main__":
    unittest.main()
