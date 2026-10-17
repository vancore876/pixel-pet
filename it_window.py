"""Separate, short-lived office IT session and employee account tools."""
from __future__ import annotations

from datetime import datetime, timezone
import time

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QDialog, QFormLayout,
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QPushButton, QScrollArea, QStackedWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget)

from ui_style import apply_window_style
from work_chat import _ApiClient, normalize_server_url, USERNAME_RE

IDLE_LOCK_SECONDS = 15 * 60


def _label(text="", name=""):
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    if name:
        label.setObjectName(name)
    return label


def _button(text, callback, name=""):
    button = QPushButton(text)
    button.setAutoDefault(False)
    button.setDefault(False)
    button.clicked.connect(callback)
    if name:
        button.setObjectName(name)
    return button


def _password():
    edit = QLineEdit()
    edit.setEchoMode(QLineEdit.Password)
    edit.setMaxLength(128)
    return edit


def _count(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 2**63 - 1


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("Invalid date")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Missing time zone")
    return stamp


def _date_label(value):
    return _timestamp(value).astimezone().strftime("%Y-%m-%d %H:%M")


def _backup_size(value):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:,.0f} {unit}" if unit == "B" else f"{value:,.1f} {unit}"
        value /= 1024


class BackupActionDialog(QDialog):
    """A fresh IT password authorizes one server-side backup job."""

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self._reply = None
        self._active = True
        self._busy = False
        self.setWindowModality(Qt.WindowModal)
        self.setWindowTitle("Back up office data · Jeffery IT")
        self.resize(460, 240)
        layout = QVBoxLayout(self)
        layout.addWidget(_label("Back up office data", "sectionTitle"))
        layout.addWidget(_label("Create and verify a backup on the office server. Employees can keep working while it runs."))
        form = QFormLayout()
        self.admin_password = _password()
        form.addRow("Your current IT password", self.admin_password)
        layout.addLayout(form)
        self.status = _label("", "hint")
        layout.addWidget(self.status)
        row = QHBoxLayout()
        row.addStretch()
        self.cancel_button = _button("Cancel", self.reject)
        self.submit_button = _button("Back up now", self.submit)
        row.addWidget(self.cancel_button)
        row.addWidget(self.submit_button)
        layout.addLayout(row)
        apply_window_style(self, owner.settings)

    def submit(self):
        if self._busy or not self._active or not self.owner._token or self.owner._must_change_password:
            return
        password = self.admin_password.text()
        if not password:
            self.status.setText("Enter your current IT password.")
            return
        self.admin_password.clear()
        self._busy = True
        self.submit_button.setEnabled(False)
        self.cancel_button.setText("Close")
        self.status.setText("Starting backup… Closing this dialog does not stop a backup already started on the server.")

        def finished(data, error, status):
            if not self._active:
                return
            self._busy = False
            self.submit_button.setEnabled(True)
            if status == 404:
                self.status.setText("Update and restart the office server to use backups.")
                return
            if error and status != 409:
                self.status.setText(error)
                return
            self.owner.backup_status.setText("A backup is already running on the server." if status == 409
                                            else "Backup started. Verifying the saved copy…")
            self.owner._backups_running = True
            self.owner._sync_backup_poll()
            self.owner.refresh_backups(force=True)
            self.accept()

        self._reply = self.owner._request("POST", "/api/it/backups", {"admin_password": password}, finished)

    def done(self, result):
        self._active = False
        if self._reply is not None:
            self.owner._cancel_request(self._reply)
            self._reply = None
        self.admin_password.clear()
        super().done(result)


class EmployeeActionDialog(QDialog):
    """Ask for fresh credentials for one explicitly selected employee."""

    def __init__(self, owner, employee, reset_password):
        super().__init__(owner)
        self.owner, self.employee = owner, dict(employee)
        self.reset_password = reset_password
        self._reply = None
        self._active = True
        self._busy = False
        self.setWindowModality(Qt.WindowModal)
        title = "Reset employee password" if reset_password else "Sign employee out of all PCs"
        self.setWindowTitle(title + " · Jeffery IT")
        self.resize(480, 360)
        layout = QVBoxLayout(self)
        layout.addWidget(_label(title, "sectionTitle"))
        detail = ("This replaces the employee's password, invalidates their recovery code, and signs them out of all PCs."
                  if reset_password else "The employee must sign in again on every PC. Their password stays the same.")
        layout.addWidget(_label(detail))
        layout.addWidget(_label("Employee: " + employee["username"]))
        form = QFormLayout()
        self.admin_password = _password()
        form.addRow("Your IT password", self.admin_password)
        self.new_password = _password()
        self.confirm_password = _password()
        if reset_password:
            form.addRow("New employee password (12–128 characters)", self.new_password)
            form.addRow("Repeat new password", self.confirm_password)
        else:
            self.new_password.setParent(self)
            self.confirm_password.setParent(self)
            self.new_password.hide()
            self.confirm_password.hide()
        self.confirm_username = QLineEdit()
        self.confirm_username.setMaxLength(32)
        form.addRow("Type the exact employee username", self.confirm_username)
        layout.addLayout(form)
        self.status = _label("", "hint")
        layout.addWidget(self.status)
        row = QHBoxLayout()
        row.addStretch()
        self.cancel_button = _button("Cancel", self.reject)
        self.submit_button = _button("Reset password" if reset_password else "Sign out all PCs", self.submit)
        row.addWidget(self.cancel_button)
        row.addWidget(self.submit_button)
        layout.addLayout(row)
        apply_window_style(self, owner.settings)

    def submit(self):
        if self._busy or not self._active or not self.owner._token:
            return
        if self.confirm_username.text() != self.employee["username"]:
            self.status.setText("Type the employee username exactly as shown.")
            return
        if not self.admin_password.text():
            self.status.setText("Enter your current IT password.")
            return
        body = {"admin_password": self.admin_password.text(),
                "confirm_username": self.confirm_username.text()}
        if self.reset_password:
            password = self.new_password.text()
            if not 12 <= len(password) <= 128:
                self.status.setText("Use 12–128 characters for the new employee password.")
                return
            if password != self.confirm_password.text():
                self.status.setText("The new passwords do not match.")
                return
            body["new_password"] = password
        self._clear_secrets()
        self._busy = True
        self.submit_button.setEnabled(False)
        self.status.setText("Updating employee account…")
        action = "reset-password" if self.reset_password else "sign-out"

        def finished(data, error, status):
            if not self._active:
                return
            self._busy = False
            self.submit_button.setEnabled(True)
            if error:
                self.status.setText(error)
                return
            username = self.employee["username"]
            message = (f"Password reset for {username}. Give them the new password privately; they can generate a new recovery code after signing in."
                       if self.reset_password else f"{username} has been signed out of all PCs.")
            self.owner.status.setText(message)
            self.owner.refresh(preserve_status=True)
            self.accept()

        self._reply = self.owner._request("POST", f"/api/it/users/{self.employee['id']}/{action}", body, finished)

    def _clear_secrets(self):
        self.admin_password.clear()
        self.new_password.clear()
        self.confirm_password.clear()

    def done(self, result):
        self._active = False
        if self._reply is not None:
            self.owner._cancel_request(self._reply)
            self._reply = None
        self._clear_secrets()
        self.confirm_username.clear()
        super().done(result)


class ITWindow(QDialog):
    """Administrator tools never reuse or save an employee's Work Chat session."""

    home_requested = Signal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.api = _ApiClient(self)
        self._logout_api = _ApiClient(self)
        self._token = ""
        self._must_change_password = False
        self._generation = 0
        self._requests = []
        self._busy = False
        self._refreshing = False
        self._expires_at = None
        self._last_activity = time.monotonic()
        self._users = []
        self._action_dialog = None
        self._backup_dialog = None
        self._backups_available = False
        self._backups_running = False
        self._backups_refreshing = False
        self._backup_refresh_reply = None
        self._backup_refresh_id = 0
        self._backup_poll = QTimer(self)
        self._backup_poll.setInterval(2_000)
        self._backup_poll.setSingleShot(True)
        self._backup_poll.timeout.connect(self.refresh_backups)
        self._stopped = False
        self.setWindowTitle("Jeffery · IT administration")
        self.resize(920, 720)
        self.setMinimumSize(700, 520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        header = QHBoxLayout()
        header.addWidget(_label("IT administration", "pageHeading"), 1)
        self.home_button = _button("Home", self._home)
        self.lock_button = _button("Lock IT", lambda: self._lock("IT is locked. Sign in to continue."))
        header.addWidget(self.home_button)
        header.addWidget(self.lock_button)
        layout.addLayout(header)
        self.status = _label("Sign in with the office IT account.", "hint")
        layout.addWidget(self.status)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        self._build_login()
        self._build_password()
        self._build_dashboard()
        self._idle_timer = QTimer(self)
        self._idle_timer.setInterval(15_000)
        self._idle_timer.timeout.connect(self._check_timeout)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self.server_input.setText(str(settings["work_chat_server"] or ""))
        self.server_input.textChanged.connect(self._address_changed)
        self.configure()
        self._update_controls()

    def _build_login(self):
        self.login_page = QWidget()
        layout = QVBoxLayout(self.login_page)
        layout.addWidget(_label("Manage office access", "sectionTitle"))
        layout.addWidget(_label("Review employee accounts and office totals, reset employee passwords, and sign an employee out of all PCs."))
        form = QFormLayout()
        self.server_input = QLineEdit()
        self.server_input.setPlaceholderText("192.168.50.194:8765 or https://office.example")
        self.server_input.setMaxLength(2048)
        self.username_input = QLineEdit("admin")
        self.username_input.setMaxLength(32)
        self.password_input = _password()
        form.addRow("Office server", self.server_input)
        form.addRow("IT username", self.username_input)
        form.addRow("IT password", self.password_input)
        layout.addLayout(form)
        self.login_button = _button("Sign in to IT", self.login, "primary")
        layout.addWidget(self.login_button)
        self.password_input.returnPressed.connect(self.login)
        self.username_input.returnPressed.connect(lambda: self.password_input.setFocus())
        self.server_input.returnPressed.connect(lambda: self.password_input.setFocus())
        layout.addWidget(_label("First setup: open IT on the office server PC, use 127.0.0.1:8765, username admin, and password admin123. You must choose a new IT password before using the tools. After setup, use the office server address on other PCs.", "hint"))
        layout.addWidget(_label("IT signs out when this window closes and locks after 15 minutes without activity. Passwords and IT sessions are never saved on this PC.", "hint"))
        layout.addStretch()
        self.pages.addWidget(self.login_page)

    def _build_password(self):
        self.password_page = QWidget()
        layout = QVBoxLayout(self.password_page)
        self.password_heading = _label("Choose a new IT password", "sectionTitle")
        layout.addWidget(self.password_heading)
        self.password_hint = _label("Replace the setup password before managing employee accounts.")
        layout.addWidget(self.password_hint)
        form = QFormLayout()
        self.current_password = _password()
        self.new_password = _password()
        self.confirm_password = _password()
        form.addRow("Current IT password", self.current_password)
        form.addRow("New IT password (12–128 characters)", self.new_password)
        form.addRow("Repeat new IT password", self.confirm_password)
        layout.addLayout(form)
        row = QHBoxLayout()
        self.password_back_button = _button("Back", self._password_back)
        self.change_password_button = _button("Change IT password", self.change_password, "primary")
        row.addWidget(self.password_back_button)
        row.addWidget(self.change_password_button)
        layout.addLayout(row)
        self.current_password.returnPressed.connect(lambda: self.new_password.setFocus())
        self.new_password.returnPressed.connect(lambda: self.confirm_password.setFocus())
        self.confirm_password.returnPressed.connect(self.change_password)
        layout.addStretch()
        self.pages.addWidget(self.password_page)

    def _build_dashboard(self):
        self.dashboard_page = QScrollArea()
        self.dashboard_page.setWidgetResizable(True)
        self.dashboard_page.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        self.connection_label = _label("", "subtitle")
        layout.addWidget(self.connection_label)
        metrics = QGridLayout()
        self.metrics = {}
        for column, (key, title) in enumerate((("users", "Employee accounts"), ("sessions", "Active sessions"),
                                              ("messages", "Messages"), ("inventory_items", "Inventory items"))):
            card = QFrame()
            card.setObjectName("surface")
            stack = QVBoxLayout(card)
            value = _label("—", "metricValue")
            value.setMinimumHeight(38)
            stack.addWidget(value)
            stack.addWidget(_label(title, "hint"))
            self.metrics[key] = value
            metrics.addWidget(card, 0, column)
        layout.addLayout(metrics)
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find an employee by username")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter_users)
        self.refresh_button = _button("Refresh", self.refresh)
        self.password_button = _button("Change IT password", self._show_password)
        row.addWidget(self.search, 1)
        row.addWidget(self.refresh_button)
        row.addWidget(self.password_button)
        layout.addLayout(row)
        self.users_table = QTableWidget(0, 3)
        self.users_table.setHorizontalHeaderLabels(["Employee", "Account created", "Active sessions"])
        self.users_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.users_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.users_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.users_table.setAlternatingRowColors(True)
        self.users_table.verticalHeader().hide()
        self.users_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.users_table.setMinimumHeight(220)
        self.users_table.itemSelectionChanged.connect(self._update_controls)
        layout.addWidget(self.users_table)
        self.user_hint = _label("Select an employee to manage access.", "hint")
        layout.addWidget(self.user_hint)
        row = QHBoxLayout()
        self.reset_button = _button("Reset employee password…", lambda: self._employee_action(True))
        self.sign_out_button = _button("Sign employee out of all PCs…", lambda: self._employee_action(False))
        row.addWidget(self.reset_button)
        row.addWidget(self.sign_out_button)
        row.addStretch()
        layout.addLayout(row)
        self._build_backups(layout)
        layout.addWidget(_label("Recent IT actions", "sectionTitle"))
        self.audit_list = QListWidget()
        self.audit_list.setMinimumHeight(140)
        layout.addWidget(self.audit_list)
        self.dashboard_page.setWidget(content)
        self.pages.addWidget(self.dashboard_page)

    def _build_backups(self, layout):
        layout.addWidget(_label("Office backups", "sectionTitle"))
        self.backup_summary = _label("Sign in to check office backups.")
        self.backup_dates = _label("", "hint")
        self.backup_status = _label("", "hint")
        self.backup_directory = _label("", "hint")
        self.backup_directory.setTextInteractionFlags(Qt.TextSelectableByMouse)
        for label in (self.backup_summary, self.backup_dates, self.backup_status, self.backup_directory):
            layout.addWidget(label)
        row = QHBoxLayout()
        self.backup_now_button = _button("Back up now…", self._backup_action)
        self.backup_refresh_button = _button("Refresh backups", self.refresh_backups)
        self.backup_restore_button = _button("Restore on server", self._toggle_restore_help)
        self.backup_restore_button.setCheckable(True)
        row.addWidget(self.backup_now_button)
        row.addWidget(self.backup_refresh_button)
        row.addWidget(self.backup_restore_button)
        row.addStretch()
        layout.addLayout(row)
        self.backup_restore_help = _label(
            "On the office server PC, stop the office server and follow the backup restore instructions in WORK_CHAT.md. "
            "Choose a verified backup listed below. Restore replaces the office database; restart the server after the restore finishes. "
            "Local notes and shared-folder files need their own backups.", "hint")
        self.backup_restore_help.hide()
        layout.addWidget(self.backup_restore_help)
        self.backups_table = QTableWidget(0, 4)
        self.backups_table.setHorizontalHeaderLabels(["Backup", "Created", "Size", "Verified"])
        self.backups_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.backups_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.backups_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.backups_table.setAlternatingRowColors(True)
        self.backups_table.verticalHeader().hide()
        self.backups_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2, 3):
            self.backups_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.backups_table.setMinimumHeight(150)
        layout.addWidget(self.backups_table)

    def _toggle_restore_help(self, checked=False):
        self.backup_restore_help.setVisible(bool(checked))

    def _backup_action(self):
        if (not self._token or self._must_change_password or self._busy or not self._backups_available
                or self._backups_running or self._backups_refreshing):
            return
        if self._backup_dialog is not None and self._backup_dialog.isVisible():
            self._backup_dialog.raise_()
            return
        if self._backup_dialog is not None:
            self._backup_dialog.deleteLater()
        self._backup_dialog = BackupActionDialog(self)
        self._backup_dialog.show()
        self._backup_dialog.admin_password.setFocus()

    def _sync_backup_poll(self):
        if (self._backups_running and self._token and not self._must_change_password and not self._stopped
                and self.isVisible() and self.pages.currentWidget() is self.dashboard_page):
            self._backup_poll.start()
        else:
            self._backup_poll.stop()

    def refresh_backups(self, checked=False, *, force=False):
        if (not self._token or self._must_change_password or self._busy or self._stopped
                or not self.isVisible() or self.pages.currentWidget() is not self.dashboard_page):
            self._backup_poll.stop()
            return
        if self._backups_refreshing:
            if not force:
                return
            if self._backup_refresh_reply is not None:
                self._cancel_request(self._backup_refresh_reply)
        self._backup_poll.stop()
        self._backup_refresh_id += 1
        request_id = self._backup_refresh_id
        self._backups_refreshing = True
        self._update_controls()

        def finished(data, error, status):
            if request_id != self._backup_refresh_id:
                return
            self._backups_refreshing = False
            self._backup_refresh_reply = None
            if status == 404:
                self._clear_backups()
                self.backup_status.setText("Update and restart the office server to use backups. Other IT tools are available.")
            elif error:
                self.backup_status.setText("Could not refresh backups. " + error)
            else:
                try:
                    self._set_backups(data)
                except (ValueError, TypeError, KeyError, OverflowError):
                    self._clear_backups()
                    self.backup_status.setText("The server returned invalid backup details. Refresh or update the office server.")
            self._update_controls()
            self._sync_backup_poll()

        self._backup_refresh_reply = self._request("GET", "/api/it/backups", None, finished)

    def _set_backups(self, data):
        if (not isinstance(data, dict) or not isinstance(data.get("enabled"), bool)
                or not isinstance(data.get("running"), bool) or not _count(data.get("keep_days"))
                or not 1 <= data["keep_days"] <= 36500):
            raise ValueError("Invalid backup settings")
        directory, error = data.get("directory"), data.get("last_error")
        if (not isinstance(directory, str) or len(directory) > 4096 or any(ord(char) < 32 for char in directory)
                or (error is not None and (not isinstance(error, str) or len(error) > 1000))):
            raise ValueError("Invalid backup details")
        last = _date_label(data["last_success_at"]) if data.get("last_success_at") else "None yet"
        next_due = _date_label(data["next_due"]) if data.get("next_due") else "Not scheduled"
        backups = data.get("backups")
        if not isinstance(backups, list) or len(backups) > 1000:
            raise ValueError("Invalid backup list")
        rows, seen = [], set()
        for backup in backups:
            if not isinstance(backup, dict):
                raise ValueError("Invalid backup")
            name = backup.get("name")
            if (not isinstance(name, str) or not 1 <= len(name) <= 255 or name in (".", "..")
                    or name in seen or any(char in "/\\" or ord(char) < 32 for char in name)
                    or not _count(backup.get("size")) or not isinstance(backup.get("verified"), bool)):
                raise ValueError("Invalid backup metadata")
            seen.add(name)
            rows.append((name, _date_label(backup.get("created_at")), _backup_size(backup["size"]),
                         "Yes" if backup["verified"] else "No"))
        self._backups_available = True
        self._backups_running = data["running"]
        self.backup_summary.setText(f"Automatic daily backups: {'On' if data['enabled'] else 'Off'} · Keep latest {data['keep_days']:,} backup dates")
        self.backup_dates.setText(f"Last verified backup: {last} · Next due: {next_due}")
        self.backup_directory.setText("Saved on the server: " + directory)
        self.backup_status.setText("Backup running; the saved copy will be verified." if data["running"]
                                   else "Last backup error: " + error if error
                                   else "Backups are ready." if rows else "No verified backups yet. Use Back up now to create the first copy.")
        self.backups_table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                self.backups_table.setItem(row, column, QTableWidgetItem(value))

    def _clear_backups(self):
        self._backup_refresh_id += 1
        self._backups_available = self._backups_running = self._backups_refreshing = False
        self._backup_refresh_reply = None
        self._backup_poll.stop()
        self.backup_summary.setText("Sign in to check office backups.")
        self.backup_dates.clear()
        self.backup_status.clear()
        self.backup_directory.clear()
        self.backups_table.setRowCount(0)
        self.backup_restore_button.setChecked(False)
        self.backup_restore_help.hide()

    def configure(self, settings=None):
        if settings is not None:
            self.settings = settings
        apply_window_style(self, self.settings)
        if self._action_dialog is not None and self._action_dialog.isVisible():
            apply_window_style(self._action_dialog, self.settings)
        if self._backup_dialog is not None and self._backup_dialog.isVisible():
            apply_window_style(self._backup_dialog, self.settings)

    @property
    def token(self):
        return self._token

    def _request(self, method, path, body, callback, *, authenticated=True):
        generation = self._generation
        token = self._token if authenticated else ""
        address = self.api.base_url
        issuing_session = path in ("/api/it/login", "/api/it/password")
        request_state = {"reply": None, "finished": False}

        def finished(data, error, status):
            request_state["finished"] = True
            self._requests = [(reply, issuing) for reply, issuing in self._requests
                              if reply is not request_state["reply"]]
            if generation != self._generation or self._stopped:
                # A request can finish as the window is locked. Discard its
                # session locally and revoke any late-issued token if available.
                late = data.get("token") if isinstance(data, dict) else None
                if (issuing_session and not error
                        and isinstance(late, str) and 16 <= len(late) <= 512
                        and late.isascii() and all(33 <= ord(char) <= 126 for char in late)):
                    self._logout_api.base_url = address
                    self._logout_api.request("POST", "/api/it/logout", {}, lambda *_: None, token=late)
                return
            if authenticated and status == 401:
                self._lock("Your IT session ended or the password was incorrect. Sign in again.")
                return
            if status == 404 and path in ("/api/it/login", "/api/it/overview", "/api/it/users"):
                error = "Update and restart the office server to use IT administration."
            callback(data, error, status)

        reply = self.api.request(method, path, body, finished, token=token)
        request_state["reply"] = reply
        if not request_state["finished"]:
            self._requests.append((reply, issuing_session))
        return reply

    def _cancel_request(self, target):
        self._requests = [(reply, issuing) for reply, issuing in self._requests if reply is not target]
        self.api.cancel(target)

    def _cancel_pending(self):
        # Let bounded login/password requests finish after lock so a token issued
        # just before closing can still be revoked. Their generation guards never
        # restore local access. The transport enforces a ten-second timeout.
        for reply, issuing in list(self._requests):
            if not issuing:
                self._cancel_request(reply)

    def login(self):
        if self._busy or self._token or self._stopped:
            return
        try:
            address = normalize_server_url(self.server_input.text())
        except ValueError as error:
            self.status.setText(str(error))
            return
        username, password = self.username_input.text().strip(), self.password_input.text()
        if username != "admin" or not password:
            self.status.setText("Enter the IT username admin and its password.")
            return
        self._generation += 1
        self._cancel_pending()
        self.api.base_url = address
        self.password_input.clear()
        self._busy = True
        self._update_controls()
        self.status.setText("Signing in to IT…")

        def finished(data, error, status):
            self._busy = False
            if error:
                self.status.setText(error)
            elif not self._accept_session(data):
                self.status.setText("The server returned an invalid IT session. Update the office server and try again.")
            self._update_controls()

        self._request("POST", "/api/it/login", {"username": username, "password": password}, finished, authenticated=False)

    def _accept_session(self, data):
        if not isinstance(data, dict):
            return False
        token = data.get("token")
        if (not isinstance(token, str) or not 16 <= len(token) <= 512
                or not token.startswith("it_")
                or not token.isascii() or any(ord(char) < 33 or ord(char) > 126 for char in token)
                or data.get("username") != "admin" or not isinstance(data.get("must_change_password"), bool)):
            return False
        try:
            expiry = _timestamp(data.get("expires_at"))
            if expiry <= datetime.now(timezone.utc):
                return False
        except (ValueError, TypeError, OverflowError):
            return False
        self._token = token
        self._expires_at = expiry
        self._must_change_password = data["must_change_password"]
        self._last_activity = time.monotonic()
        self._idle_timer.start()
        if self._must_change_password:
            self.status.setText("Choose a new IT password to complete setup.")
            self._show_password()
        else:
            self.pages.setCurrentWidget(self.dashboard_page)
            self.connection_label.setText("Office server: " + self.api.base_url)
            self.status.setText("Signed in to IT. Employee sessions remain separate.")
            self.refresh(preserve_status=True)
        return True

    def _show_password(self):
        if not self._token or self._busy:
            return
        self._clear_password_fields()
        self.password_hint.setText("Replace the setup password before managing employee accounts." if self._must_change_password
                                   else "Changing the IT password signs out other IT sessions. Employee passwords stay the same.")
        self.password_back_button.setVisible(not self._must_change_password)
        self.pages.setCurrentWidget(self.password_page)
        self._backup_poll.stop()
        self.current_password.setFocus()

    def _password_back(self):
        if self._must_change_password or self._busy:
            return
        self._clear_password_fields()
        self.pages.setCurrentWidget(self.dashboard_page)
        self.refresh_backups()

    def change_password(self):
        if self._busy or not self._token or self.pages.currentWidget() is not self.password_page:
            return
        current, password = self.current_password.text(), self.new_password.text()
        if not current:
            self.status.setText("Enter your current IT password again.")
            return
        if not 12 <= len(password) <= 128:
            self.status.setText("Use 12–128 characters for the new IT password.")
            return
        if password != self.confirm_password.text():
            self.status.setText("The new IT passwords do not match.")
            return
        self._clear_password_fields()
        self._busy = True
        self._update_controls()
        self.status.setText("Changing IT password…")

        def finished(data, error, status):
            self._busy = False
            if error:
                self.status.setText(error)
            elif not self._accept_session(data):
                self._lock("Password update completed, but the session response was invalid. Sign in with your new password.")
            else:
                self.status.setText("IT password changed. Other IT sessions have been signed out.")
            self._update_controls()

        self._request("POST", "/api/it/password", {"current_password": current, "new_password": password}, finished)

    def refresh(self, checked=False, *, preserve_status=False):
        if not self._token or self._must_change_password or self._refreshing or self._busy:
            return
        self._refreshing = True
        self._update_controls()
        if not preserve_status:
            self.status.setText("Refreshing office accounts…")
        pending = {"overview", "users"}
        failures = []

        def finish(kind, data, error, status):
            if not error:
                try:
                    self._overview(data) if kind == "overview" else self._set_users(data)
                except (ValueError, TypeError, KeyError, OverflowError):
                    error = "The server returned invalid IT data. Update the office server and try again."
            if error:
                failures.append(error)
            pending.discard(kind)
            if not pending:
                self._refreshing = False
                self._update_controls()
                if failures:
                    self.status.setText(failures[0])
                elif not preserve_status:
                    self.status.setText("Office accounts are up to date.")

        self._request("GET", "/api/it/overview", None, lambda *args: finish("overview", *args))
        self._request("GET", "/api/it/users", None, lambda *args: finish("users", *args))
        self.refresh_backups()

    def _overview(self, data):
        if not isinstance(data, dict) or any(not _count(data.get(key)) for key in self.metrics):
            raise ValueError("Invalid counts")
        audit = data.get("audit")
        if not isinstance(audit, list) or len(audit) > 100:
            raise ValueError("Invalid audit")
        rows = []
        names = {"initialized": "IT account initialized", "login": "IT signed in", "logout": "IT signed out",
                 "password_changed": "IT password changed", "employee_password_reset": "Employee password reset",
                 "employee_sessions_revoked": "Employee signed out of all PCs", "local_password_reset": "IT password reset on server"}
        for row in audit:
            if not isinstance(row, dict) or not isinstance(row.get("action"), str) or len(row["action"]) > 100:
                raise ValueError("Invalid audit entry")
            target = row.get("target_username")
            if target is not None and (not isinstance(target, str) or len(target) > 64):
                raise ValueError("Invalid audit username")
            stamp = _date_label(row.get("created_at"))
            action = names.get(row["action"], row["action"].replace("_", " "))
            rows.append(f"{stamp} · {action}" + (f" · {target}" if target else ""))
        for key, label in self.metrics.items():
            label.setText(f"{data[key]:,}")
        self.audit_list.clear()
        self.audit_list.addItems(rows or ["No recent IT actions."])

    def _set_users(self, data):
        users = data.get("users") if isinstance(data, dict) else None
        if not isinstance(users, list) or len(users) > 1000:
            raise ValueError("Invalid employee list")
        seen = set()
        for user in users:
            if (not isinstance(user, dict) or not _count(user.get("id")) or user["id"] == 0
                    or user["id"] in seen or not isinstance(user.get("username"), str)
                    or not USERNAME_RE.fullmatch(user["username"]) or not _count(user.get("active_sessions"))):
                raise ValueError("Invalid employee")
            _timestamp(user.get("created_at"))
            seen.add(user["id"])
        self._users = [dict(user) for user in users]
        self._filter_users()

    def _filter_users(self):
        selected = self._selected_user()
        selected_id = selected["id"] if selected else None
        query = self.search.text().strip().casefold()
        users = [user for user in self._users if query in user["username"].casefold()]
        self.users_table.setRowCount(0)
        self.users_table.setRowCount(len(users))
        for row, user in enumerate(users):
            for column, text in enumerate((user["username"], _date_label(user["created_at"]), str(user["active_sessions"]))):
                item = QTableWidgetItem(text)
                item.setData(Qt.UserRole, user["id"])
                self.users_table.setItem(row, column, item)
            if user["id"] == selected_id:
                self.users_table.selectRow(row)
        self._update_controls()

    def _selected_user(self):
        row = self.users_table.currentRow()
        item = self.users_table.item(row, 0) if row >= 0 else None
        return next((user for user in self._users if item is not None and user["id"] == item.data(Qt.UserRole)), None)

    def _employee_action(self, reset_password):
        employee = self._selected_user()
        if not employee or not self._token or self._must_change_password or self._busy or self._refreshing:
            return
        if self._action_dialog is not None and self._action_dialog.isVisible():
            self._action_dialog.raise_()
            return
        if self._action_dialog is not None:
            self._action_dialog.deleteLater()
        self._action_dialog = EmployeeActionDialog(self, employee, reset_password)
        self._action_dialog.show()
        self._action_dialog.admin_password.setFocus()

    def _update_controls(self):
        self.login_button.setEnabled(not self._busy and not self._token and not self._stopped)
        self.lock_button.setEnabled(bool(self._token) or self._busy)
        self.change_password_button.setEnabled(bool(self._token) and not self._busy)
        self.password_back_button.setEnabled(not self._busy)
        self.refresh_button.setEnabled(bool(self._token) and not self._refreshing and not self._busy)
        self.password_button.setEnabled(bool(self._token) and not self._busy and not self._refreshing)
        backup_access = bool(self._token and not self._must_change_password and not self._busy and not self._stopped)
        self.backup_refresh_button.setEnabled(backup_access and not self._backups_refreshing)
        self.backup_now_button.setEnabled(backup_access and self._backups_available and not self._backups_running
                                         and not self._backups_refreshing)
        self.backup_restore_button.setEnabled(backup_access)
        employee = self._selected_user()
        available = bool(employee and self._token and not self._must_change_password and not self._busy and not self._refreshing)
        self.reset_button.setEnabled(available)
        self.sign_out_button.setEnabled(available)
        self.user_hint.setText("Selected: " + employee["username"] if employee else "Select an employee to manage access.")

    def _clear_password_fields(self):
        for field in (self.password_input, self.current_password, self.new_password, self.confirm_password):
            field.clear()

    def _lock(self, message="IT is locked. Sign in to continue.", *, revoke=True):
        token, address = self._token, self.api.base_url
        self._generation += 1
        self._cancel_pending()
        self._token = ""
        self._must_change_password = False
        self._expires_at = None
        self._busy = self._refreshing = False
        self._idle_timer.stop()
        if self._action_dialog is not None:
            self._action_dialog.reject()
        if self._backup_dialog is not None:
            self._backup_dialog.reject()
        self._clear_backups()
        self._clear_password_fields()
        self._users = []
        self.search.clear()
        self.users_table.setRowCount(0)
        self.audit_list.clear()
        self.connection_label.clear()
        for label in self.metrics.values():
            label.setText("—")
        self.pages.setCurrentWidget(self.login_page)
        self.status.setText(message)
        self._update_controls()
        if token and address and revoke:
            self._logout_api.base_url = address
            self._logout_api.request("POST", "/api/it/logout", {}, lambda *_: None, token=token)

    def _address_changed(self):
        self.password_input.clear()
        if self._token or self._busy:
            self._lock("Server address changed. Sign in to the selected server.")

    def _check_timeout(self):
        if not self._token:
            return
        if self._expires_at is not None and datetime.now(timezone.utc) >= self._expires_at:
            self._lock("Your IT session expired. Sign in again.")
        elif time.monotonic() - self._last_activity >= IDLE_LOCK_SECONDS:
            self._lock("IT locked after 15 minutes without activity. Sign in again.")

    def eventFilter(self, watched, event):
        if (self._token and event.type() in (QEvent.KeyPress, QEvent.MouseButtonPress, QEvent.MouseMove, QEvent.Wheel, QEvent.TouchBegin)
                and isinstance(watched, QWidget) and (watched is self or self.isAncestorOf(watched))):
            self._last_activity = time.monotonic()
        return super().eventFilter(watched, event)

    def _home(self):
        self.close()
        self.home_requested.emit()

    def reject(self):
        self._lock()
        super().reject()

    def closeEvent(self, event):
        self._lock()
        super().closeEvent(event)

    def hideEvent(self, event):
        self._backup_poll.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if self._token and not self._must_change_password:
            self.refresh_backups()

    def shutdown(self):
        self._stopped = True
        self._lock()
        self.hide()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
