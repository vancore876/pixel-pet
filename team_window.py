"""Employee inbox, explicit message composition, and shared-folder setup."""
from __future__ import annotations

from PySide6.QtCore import Qt, QDateTime, Signal, QSignalBlocker
from PySide6.QtWidgets import (QApplication, QDialog, QWidget, QVBoxLayout,
    QHBoxLayout, QFormLayout, QTabWidget, QSplitter, QListWidget, QListWidgetItem,
    QLineEdit, QPlainTextEdit, QPushButton, QCheckBox, QComboBox, QSpinBox,
    QDateTimeEdit, QFileDialog)

from team_popup import team_style, plain_label
from message_input import MessageInput


def _setting(settings, key, default=None):
    try:
        return settings[key]
    except KeyError:
        return default


def _reminder_label(row):
    if row.get("acknowledged"):
        return "Acknowledged · your reminders stopped"
    due = row.get("next_due")
    if due is None:
        return "Read" if row.get("read") else "Unread"
    return "Next reminder: " + QDateTime.fromSecsSinceEpoch(int(due)).toString("ddd, MMM d · h:mm AP")


class TeamWindow(QDialog):
    preferences_requested = Signal(object)
    notebook_requested = Signal()
    source_requested = Signal(str)
    assignment_requested = Signal(str, object)

    def __init__(self, service, settings):
        super().__init__()
        self.service, self.settings = service, settings
        self.source_note_id = ""
        self.message_draft = None
        self.pending_messages = []
        self.error_count = 0
        self.selected = None
        self.refreshing = False
        self.setWindowTitle("Jeffery · Team messages")
        screen = QApplication.primaryScreen()
        self.resize(880, min(700, screen.availableGeometry().height() - 60) if screen else 700)
        self.setMinimumSize(670, 480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        header = QHBoxLayout()
        title = plain_label("Your team, together")
        title.setStyleSheet("font-size: 21px; font-weight: 600;")
        header.addWidget(title, 1)
        notebook = QPushButton("Shared notebook / customer reminders")
        notebook.clicked.connect(self.notebook_requested.emit)
        header.addWidget(notebook)
        layout.addLayout(header)
        self.connection = plain_label()
        self.connection.setObjectName("hint")
        layout.addWidget(self.connection)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self._build_inbox()
        self._build_composer()
        self._build_setup()
        self.status = plain_label()
        self.status.setObjectName("hint")
        layout.addWidget(self.status)
        self.service.changed.connect(self.refresh)
        self.service.error.connect(self.show_error)
        self.service.status_changed.connect(self.status.setText)
        self.service.message_queued.connect(self.message_saved)
        self.configure()
        self.refresh()
        if not _setting(settings, "team_sync_enabled", False):
            self.tabs.setCurrentWidget(self.setup_page)

    def _page(self, title):
        page = QWidget()
        page.setObjectName("teamPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        self.tabs.addTab(page, title)
        return page, layout

    def _build_inbox(self):
        self.inbox_page, layout = self._page("My inbox")
        self.inbox_hint = plain_label("Messages and customer reminders assigned to you. Acknowledge stops only your reminders.")
        self.inbox_hint.setObjectName("hint")
        layout.addWidget(self.inbox_hint)
        splitter = QSplitter()
        self.messages = QListWidget()
        self.messages.setMinimumWidth(200)
        self.messages.currentItemChanged.connect(self.select_message)
        splitter.addWidget(self.messages)
        panel = QWidget()
        detail = QVBoxLayout(panel)
        self.message_title = plain_label("Choose a message")
        self.message_title.setStyleSheet("font-size: 17px; font-weight: 600;")
        detail.addWidget(self.message_title)
        self.message_meta = plain_label()
        self.message_meta.setObjectName("hint")
        detail.addWidget(self.message_meta)
        self.message_body = QPlainTextEdit()
        self.message_body.setReadOnly(True)
        detail.addWidget(self.message_body, 1)
        actions = QHBoxLayout()
        self.acknowledge_button = QPushButton("Acknowledge")
        self.acknowledge_button.clicked.connect(self.acknowledge)
        actions.addWidget(self.acknowledge_button)
        self.snooze_minutes = QComboBox()
        for minutes in (5, 15, 30, 60):
            self.snooze_minutes.addItem(f"{minutes} min", minutes)
        self.snooze_minutes.setAccessibleName("Inbox snooze duration")
        actions.addWidget(self.snooze_minutes)
        self.snooze_button = QPushButton("Remind me later")
        self.snooze_button.clicked.connect(self.snooze)
        actions.addWidget(self.snooze_button)
        detail.addLayout(actions)
        self.open_source_button = QPushButton("Open source note / customer order")
        self.open_source_button.clicked.connect(self.open_source)
        detail.addWidget(self.open_source_button)
        splitter.addWidget(panel)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        refresh = QPushButton("Refresh team")
        refresh.clicked.connect(self.request_refresh)
        layout.addWidget(refresh)

    def _build_composer(self):
        self.compose_page, layout = self._page("Send a message")
        self.compose_hint = plain_label("Recipients get a popup, followed by their own reminders until they acknowledge it.")
        self.compose_hint.setObjectName("hint")
        layout.addWidget(self.compose_hint)
        self.source_caption = plain_label()
        self.source_caption.setObjectName("hint")
        layout.addWidget(self.source_caption)
        self.clear_source_button = QPushButton("Back to my message draft")
        self.clear_source_button.clicked.connect(self.clear_source)
        layout.addWidget(self.clear_source_button)
        form = QFormLayout()
        self.title = QLineEdit()
        self.title.setMaxLength(100)
        self.title.setPlaceholderText("A short subject")
        form.addRow("Subject", self.title)
        self.customer = QLineEdit()
        self.customer.setMaxLength(100)
        self.customer.setPlaceholderText("Optional customer name")
        form.addRow("Customer", self.customer)
        layout.addLayout(form)
        self.body = MessageInput()
        self.body.setPlaceholderText("Write the message employees should receive… Enter sends; Shift+Enter adds a line.")
        self.body.send_requested.connect(self.send)
        layout.addWidget(self.body, 1)
        self.everyone = QCheckBox("Everyone in this team")
        self.everyone.toggled.connect(self._everyone_changed)
        layout.addWidget(self.everyone)
        self.recipients = QListWidget()
        self.recipients.setMaximumHeight(145)
        layout.addWidget(self.recipients)
        reminder = QHBoxLayout()
        self.scheduled = QCheckBox("First reminder at")
        self.due = QDateTimeEdit(QDateTime.currentDateTime().addSecs(1800))
        self.due.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.due.setCalendarPopup(True)
        self.due.setEnabled(False)
        self.scheduled.toggled.connect(self.due.setEnabled)
        self.repeat = QSpinBox()
        self.repeat.setRange(0, 1440)
        self.repeat.setSuffix(" min")
        self.repeat.setValue(_setting(self.settings, "team_reminder_minutes", 30))
        reminder.addWidget(self.scheduled)
        reminder.addWidget(self.due)
        reminder.addWidget(plain_label("Repeat every"))
        reminder.addWidget(self.repeat)
        layout.addLayout(reminder)
        self.reminder_hint = plain_label("0 disables repeats. An immediate popup still appears. Messages stay queued if the shared folder is unavailable.")
        self.reminder_hint.setObjectName("hint")
        layout.addWidget(self.reminder_hint)
        self.send_button = QPushButton("Send message")
        self.send_button.setObjectName("primary")
        self.send_button.clicked.connect(self.send)
        layout.addWidget(self.send_button)
        for field in (self.title, self.customer):
            field.textChanged.connect(self._draft_changed)
        self.body.textChanged.connect(self._draft_changed)
        self.recipients.itemChanged.connect(self._draft_changed)
        self.scheduled.toggled.connect(self._draft_changed)
        self.due.dateTimeChanged.connect(self._draft_changed)
        self.repeat.valueChanged.connect(self._draft_changed)
        self.clear_source()

    def _build_setup(self):
        self.setup_page, layout = self._page("Team setup")
        hint = plain_label("Choose the same shared folder on every PC, then select the employee who uses this Windows account. Use an existing employee on their second PC.")
        hint.setObjectName("hint")
        layout.addWidget(hint)
        form = QFormLayout()
        folder_row = QHBoxLayout()
        self.shared_folder = QLineEdit(_setting(self.settings, "team_shared_folder", ""))
        self.shared_folder.setMaxLength(4096)
        self.shared_folder.setPlaceholderText(r"\\work-server\JefferyTeam")
        folder_row.addWidget(self.shared_folder, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.browse_folder)
        folder_row.addWidget(browse)
        form.addRow("Shared team folder", folder_row)
        self.employee_choice = QComboBox()
        self.employee_choice.currentIndexChanged.connect(self.employee_selected)
        form.addRow("Employee on this PC", self.employee_choice)
        self.employee_name = QLineEdit((self.service.identity() or {}).get("name") or _setting(self.settings, "team_employee_name", ""))
        self.employee_name.setMaxLength(100)
        form.addRow("Display name", self.employee_name)
        self.default_repeat = QSpinBox()
        self.default_repeat.setRange(0, 1440)
        self.default_repeat.setSuffix(" min")
        self.default_repeat.setValue(_setting(self.settings, "team_reminder_minutes", 30))
        form.addRow("Default message reminder", self.default_repeat)
        layout.addLayout(form)
        self.share_ai = QCheckBox("Allow shared notebook passages in Groq replies")
        self.share_ai.setChecked(_setting(self.settings, "team_share_notes_with_ai", False))
        layout.addWidget(self.share_ai)
        self.identity_label = plain_label()
        self.identity_label.setObjectName("hint")
        layout.addWidget(self.identity_label)
        buttons = QHBoxLayout()
        self.connect_button = QPushButton("Save and connect")
        self.connect_button.clicked.connect(self.connect_team)
        buttons.addWidget(self.connect_button)
        self.disconnect_button = QPushButton("Disconnect this PC")
        self.disconnect_button.clicked.connect(lambda: self.preferences_requested.emit({"team_sync_enabled": False}))
        buttons.addWidget(self.disconnect_button)
        layout.addLayout(buttons)
        layout.addStretch(1)

    def configure(self):
        self.setStyleSheet(team_style(self.settings))

    def browse_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Choose the shared team folder", self.shared_folder.text())
        if path:
            self.shared_folder.setText(path)

    def employee_selected(self):
        if self.refreshing:
            return
        profile = self.employee_choice.currentData()
        if isinstance(profile, dict):
            self.employee_name.setText(profile.get("name", ""))

    def connect_team(self):
        folder, name = self.shared_folder.text().strip(), self.employee_name.text().strip()
        if not folder or not name:
            self.status.setText("Choose a shared folder and enter your employee display name.")
            return
        profile = self.employee_choice.currentData() or {}
        self.preferences_requested.emit({"team_sync_enabled": True, "team_shared_folder": folder,
            "team_employee_id": profile.get("id", ""), "team_employee_name": name,
            "team_reminder_minutes": self.default_repeat.value(),
            "team_share_notes_with_ai": self.share_ai.isChecked()})

    def refresh(self):
        """Read cached service data only, preserving every composer/setup draft."""
        if self.refreshing:
            return
        self.refreshing = True
        try:
            identity = self.service.identity() or {}
            roster = self.service.roster()
            chosen = {self.recipients.item(i).data(Qt.UserRole)
                for i in range(self.recipients.count()) if self.recipients.item(i).checkState() == Qt.Checked}
            old_profile = self.employee_choice.currentData()
            had_employee_choices = self.employee_choice.count() > 0
            old_employee_id = old_profile.get("id", "") if isinstance(old_profile, dict) else None
            with QSignalBlocker(self.recipients), QSignalBlocker(self.employee_choice):
                self.recipients.clear()
                self.employee_choice.clear()
                self.employee_choice.addItem("New employee profile", {})
                for employee in roster:
                    identifier = employee["id"]
                    label = employee.get("name") or "Employee"
                    if identifier == identity.get("id"):
                        label += " (you)"
                    item = QListWidgetItem(label)
                    item.setData(Qt.UserRole, identifier)
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    item.setCheckState(Qt.Checked if identifier in chosen else Qt.Unchecked)
                    self.recipients.addItem(item)
                    self.employee_choice.addItem(label, dict(employee))
                # A copied portable settings file must never select another
                # employee. Only this PC's active profile or an explicit UI
                # choice can determine the recipient identity.
                selected_id = old_employee_id if had_employee_choices else identity.get("id")
                for index in range(1, self.employee_choice.count()):
                    if self.employee_choice.itemData(index).get("id") == selected_id:
                        self.employee_choice.setCurrentIndex(index)
                        break
            rows = self.service.inbox()
            selected_key = (self.selected or {}).get("id")
            with QSignalBlocker(self.messages):
                self.messages.clear()
                for row in rows:
                    sender = row.get("author_name", row.get("sender_name", ""))
                    state = "Acknowledged" if row.get("acknowledged") else "Read" if row.get("read") else "Unread"
                    label = str(row.get("title", "Team message")) + "\n" + " · ".join(filter(None, [str(sender), str(row.get("customer", "")), state]))
                    item = QListWidgetItem(label)
                    item.setData(Qt.UserRole, dict(row))
                    self.messages.addItem(item)
                    if row.get("id") == selected_key:
                        self.messages.setCurrentItem(item)
            self.select_message(self.messages.currentItem())
            name = identity.get("name") or _setting(self.settings, "team_employee_name", "")
            self.identity_label.setText("Active employee: " + name if name else "No employee is connected yet.")
            enabled = bool(getattr(self.service, "enabled", _setting(self.settings, "team_sync_enabled", False)))
            self.connection.setText(("Connected as " + name if enabled and name else "Team sync is disconnected") + " · " + str(getattr(self.service, "status", "")))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.status.setText("Team view could not refresh: " + str(exc))
        finally:
            self.refreshing = False

    def select_message(self, item, previous=None):
        self.selected = item.data(Qt.UserRole) if item else None
        row = self.selected or {}
        self.message_title.setText(str(row.get("title", "Choose a message")))
        sender = row.get("author_name", row.get("sender_name", ""))
        self.message_meta.setText(" · ".join(filter(None, ["From: " + str(sender) if sender else "",
            "Customer: " + str(row["customer"]) if row.get("customer") else "", _reminder_label(row) if row else ""])))
        self.message_body.setPlainText(str(row.get("body", "")))
        active = bool(row) and not row.get("acknowledged", False)
        self.acknowledge_button.setEnabled(active)
        self.snooze_button.setEnabled(active)
        self.open_source_button.setEnabled(bool(self._source_id(row)))
        if row and not row.get("read") and self.isVisible() and not self.refreshing:
            self._action(lambda: self.service.mark_read(row["id"], entity_kind=row.get("entity_kind", "message")))

    @staticmethod
    def _source_id(row):
        return row.get("note_id", row.get("source_note_id", "")) or (row.get("id", "") if row.get("entity_kind") == "note" else "")

    def open_source(self):
        if self.selected and self._source_id(self.selected):
            self.source_requested.emit(self._source_id(self.selected))

    def acknowledge(self):
        if self.selected:
            row = dict(self.selected)
            self._action(lambda: self.service.acknowledge(row["id"], entity_kind=row.get("entity_kind", "message")))

    def snooze(self):
        if self.selected:
            row = dict(self.selected)
            minutes = int(self.snooze_minutes.currentData())
            self._action(lambda: self.service.snooze(row["id"], minutes, entity_kind=row.get("entity_kind", "message")))

    def request_refresh(self):
        self._action(self.service.pump)

    def _action(self, callback):
        try:
            callback()
        except (OSError, ValueError, RuntimeError) as exc:
            self.show_error(str(exc))

    def _everyone_changed(self, checked):
        self.recipients.setEnabled(not checked)
        self._draft_changed()

    def _draft_changed(self, *args):
        if hasattr(self, "send_button") and not self.refreshing:
            self.send_button.setEnabled(True)

    def send(self):
        recipients = [] if self.everyone.isChecked() else [self.recipients.item(i).data(Qt.UserRole)
            for i in range(self.recipients.count()) if self.recipients.item(i).checkState() == Qt.Checked]
        if not self.everyone.isChecked() and not recipients:
            self.status.setText("Choose one or more employees, or select Everyone.")
            return
        if self.source_note_id:
            previous_errors = self.error_count
            self.assignment_requested.emit(self.source_note_id, recipients)
            if self.error_count != previous_errors:
                return
            self.status.setText("Customer / note reminder assignment requested. Its schedule stays in the notebook.")
            self.send_button.setEnabled(False)
            return
        title, body = self.title.text().strip(), self.body.toPlainText().strip()
        if not title or not body:
            self.status.setText("Enter a subject and message before sending.")
            return
        if len(body) > 10_000:
            self.status.setText("Messages support up to 10,000 characters. Save larger text in the notebook.")
            return
        try:
            accepted = self.service.send_message(title, body, recipients,
                first_due=self.due.dateTime().toSecsSinceEpoch() if self.scheduled.isChecked() else None,
                repeat_minutes=self.repeat.value(), customer=self.customer.text().strip(), note_id="")
        except (OSError, ValueError, RuntimeError) as exc:
            self.show_error(str(exc))
            return
        if accepted is False:
            return
        self.pending_messages.append(self._message_draft())
        self.status.setText("Saving this message to your local outbox…")
        self.send_button.setEnabled(False)

    def _message_draft(self):
        return {"title": self.title.text().strip(), "body": self.body.toPlainText().strip(),
            "customer": self.customer.text().strip(), "repeat_minutes": self.repeat.value(),
            "first_due": self.due.dateTime().toSecsSinceEpoch() if self.scheduled.isChecked() else None,
            "recipients": [] if self.everyone.isChecked() else [self.recipients.item(i).data(Qt.UserRole)
                for i in range(self.recipients.count()) if self.recipients.item(i).checkState() == Qt.Checked]}

    def message_saved(self, event):
        """Clear only an unchanged draft after its local outbox commit succeeds."""
        payload = event.get("payload", {})
        saved = next((draft for draft in self.pending_messages
            if all(draft[key] == payload.get(key) for key in ("title", "body", "customer", "repeat_minutes"))
            and set(draft["recipients"]) == set(payload.get("recipients", []))
            and (draft["first_due"] is None or draft["first_due"] == payload.get("due_at"))), None)
        if saved is None:
            return
        self.pending_messages.remove(saved)
        if not self.source_note_id and self._message_draft() == saved:
            self.title.clear()
            self.body.clear()
            self.customer.clear()
            self.send_button.setEnabled(True)
        elif self.source_note_id and self.message_draft is not None:
            original = self.message_draft
            original_signature = {"title": original["title"].strip(), "body": original["body"].strip(),
                "customer": original["customer"].strip(), "repeat_minutes": original["repeat"],
                "first_due": original["due"].toSecsSinceEpoch() if original["scheduled"] else None,
                "recipients": [] if original["everyone"] else original["recipients"]}
            if original_signature == saved:
                # A queued message can commit while the user assigns a note.
                # Returning to the composer must not resurrect that sent draft.
                original.update(title="", body="", customer="")
        self.status.setText("Message saved to your local outbox. It will be delivered when the shared folder is available.")

    def compose_note(self, note):
        """Assign a saved note; content and schedules remain editable in the notebook."""
        if not self.source_note_id:
            self.message_draft = {"title": self.title.text(), "body": self.body.toPlainText(),
                "customer": self.customer.text(), "everyone": self.everyone.isChecked(),
                "recipients": [self.recipients.item(i).data(Qt.UserRole) for i in range(self.recipients.count())
                    if self.recipients.item(i).checkState() == Qt.Checked],
                "scheduled": self.scheduled.isChecked(), "due": self.due.dateTime(),
                "repeat": self.repeat.value()}
        self.source_note_id = str(note["id"])
        self.title.setText(str(note.get("title", "")))
        self.body.setPlainText(str(note.get("body", "")))
        self.customer.setText(str(note.get("customer", "")))
        for field in (self.title, self.body, self.customer):
            field.setReadOnly(True)
        self.scheduled.setEnabled(False)
        self.due.setEnabled(False)
        self.repeat.setEnabled(False)
        self.source_caption.setText("Assign this saved note / customer order. Edit its content and reminder time in the notebook.")
        self.source_caption.show()
        self.clear_source_button.show()
        self.send_button.setText("Assign reminder")
        self.send_button.setEnabled(True)
        assigned = note.get("team_recipients", [])
        self.everyone.setChecked(not assigned)
        for index in range(self.recipients.count()):
            item = self.recipients.item(index)
            item.setCheckState(Qt.Checked if item.data(Qt.UserRole) in assigned else Qt.Unchecked)
        self.tabs.setCurrentWidget(self.compose_page)

    def clear_source(self):
        had_source = bool(self.source_note_id)
        self.source_note_id = ""
        if not hasattr(self, "send_button"):
            return
        for field in (self.title, self.body, self.customer):
            field.setReadOnly(False)
        self.scheduled.setEnabled(True)
        self.due.setEnabled(self.scheduled.isChecked())
        self.repeat.setEnabled(True)
        self.source_caption.hide()
        self.clear_source_button.hide()
        self.send_button.setText("Send message")
        self.send_button.setEnabled(True)
        if had_source and self.message_draft is not None:
            draft = self.message_draft
            self.title.setText(draft["title"])
            self.body.setPlainText(draft["body"])
            self.customer.setText(draft["customer"])
            self.everyone.setChecked(draft["everyone"])
            for index in range(self.recipients.count()):
                item = self.recipients.item(index)
                item.setCheckState(Qt.Checked if item.data(Qt.UserRole) in draft["recipients"] else Qt.Unchecked)
            self.scheduled.setChecked(draft["scheduled"])
            self.due.setDateTime(draft["due"])
            self.repeat.setValue(draft["repeat"])
            self.message_draft = None

    def open_message(self, identifier, entity_kind=None):
        self.refresh()
        self.tabs.setCurrentWidget(self.inbox_page)
        for index in range(self.messages.count()):
            item = self.messages.item(index)
            row = item.data(Qt.UserRole)
            if row.get("id") == identifier and (entity_kind is None or row.get("entity_kind", "message") == entity_kind):
                self.messages.setCurrentItem(item)
                self.select_message(item)
                return True
        self.status.setText("This message is no longer in your inbox.")
        return False

    def show_error(self, message):
        self.error_count += 1
        self.status.setText(str(message))
        self.send_button.setEnabled(True)
