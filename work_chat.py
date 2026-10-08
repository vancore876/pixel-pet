"""Office chat for coworkers, separate from Jeffery's Groq AI conversation.

Only the server address and username are saved locally. Passwords and bearer
tokens live in memory, and all network work runs asynchronously on Qt's loop.
"""
from __future__ import annotations

import ipaddress
import json
import re
from datetime import datetime
from urllib.parse import urlencode, urlsplit, urlunsplit

from PySide6.QtCore import QByteArray, QObject, QSignalBlocker, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (QCheckBox, QDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton,
    QSplitter, QStackedWidget, QVBoxLayout, QWidget)

from notepad_window import notes_style
from themes import palette

MAX_RESPONSE_BYTES = 1024 * 1024
REQUEST_TIMEOUT_MS = 10_000
MAX_VISIBLE_MESSAGES = 500
MESSAGE_BATCH_LIMIT = 50  # 4000 four-byte Unicode characters × 50 fits in 1 MiB.
USERNAME_RE = re.compile(r"[A-Za-z0-9_.-]{3,32}\Z")
_NUMERIC_HOST_RE = re.compile(r"(?:0[xX][0-9a-fA-F]+|[0-9]+)(?:\.(?:0[xX][0-9a-fA-F]+|[0-9]+)){0,3}\.?\Z")
_OFFICE_NETWORKS = tuple(ipaddress.ip_network(network) for network in
                         ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7"))
_CURRENT_CONVERSATION = object()


def normalize_server_url(value: str) -> str:
    """Accept an office IP or origin, restricting HTTP to literal private IPs."""
    value = value.strip()
    if (not value or any(char.isspace() or ord(char) < 32 for char in value)
            or "\\" in value or "%" in value):
        raise ValueError("Enter the office server's IP address, such as 192.168.50.194.")
    bare_address = "://" not in value
    if bare_address:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            value = "http://" + value
        else:
            value = "http://" + (f"[{address}]" if address.version == 6 else str(address))
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise ValueError("The server address or port is invalid.") from None
    if (parts.scheme.lower() not in ("http", "https") or not parts.hostname
            or parts.username is not None or parts.password is not None or "@" in parts.netloc
            or parts.path not in ("", "/") or "?" in value or "#" in value):
        raise ValueError("Use only the server IP or HTTP/HTTPS address and optional port, without a path or login details.")
    host = parts.hostname.lower()
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
        if _NUMERIC_HOST_RE.fullmatch(host):
            raise ValueError("Enter a complete IP address, such as 192.168.50.194.") from None
    if address is not None:
        # IPv4-mapped IPv6 addresses follow the same office range restrictions.
        checked_address = address.ipv4_mapped if address.version == 6 else None
        host = "::ffff:" + str(checked_address) if checked_address else str(address)
        checked_address = checked_address or address
        office_address = (checked_address.is_loopback or any(
            checked_address.version == network.version and checked_address in network
            for network in _OFFICE_NETWORKS))
    else:
        office_address = host == "localhost"
    if parts.scheme.lower() == "http" and not office_address:
        raise ValueError("HTTP requires a private office IP address. For a server name or public IP, use HTTPS.")
    if parts.netloc.endswith(":") or port == 0:
        raise ValueError("The server port must be between 1 and 65535.")
    if bare_address and port is None:
        port = 8765
    authority = f"[{host}]" if ":" in host else host
    if port is not None:
        authority += f":{port}"
    normalized = urlunsplit((parts.scheme.lower(), authority, "", "", ""))
    if not QUrl(normalized).isValid():
        raise ValueError("The server address is invalid.")
    return normalized


class _ApiClient(QObject):
    """Small bounded JSON transport. Redirects never receive a session token."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = QNetworkAccessManager(self)
        self.base_url = ""
        self._pending = {}

    def request(self, method, path, body, callback, token=""):
        request = QNetworkRequest(QUrl(self.base_url + path))
        request.setAttribute(QNetworkRequest.RedirectPolicyAttribute,
                             QNetworkRequest.ManualRedirectPolicy)
        request.setAttribute(QNetworkRequest.CacheLoadControlAttribute,
                             QNetworkRequest.AlwaysNetwork)
        request.setAttribute(QNetworkRequest.CacheSaveControlAttribute, False)
        request.setRawHeader(b"Accept", b"application/json")
        if token:
            request.setRawHeader(b"Authorization", ("Bearer " + token).encode("ascii"))
        if method in ("POST", "PUT", "DELETE"):
            request.setHeader(QNetworkRequest.ContentTypeHeader, "application/json")
            payload = QByteArray(json.dumps(body or {}, ensure_ascii=False).encode("utf-8"))
            if method == "POST":
                reply = self.manager.post(request, payload)
            elif method == "PUT":
                reply = self.manager.put(request, payload)
            else:
                reply = self.manager.sendCustomRequest(request, QByteArray(b"DELETE"), payload)
        else:
            reply = self.manager.get(request)
        reply.setReadBufferSize(64 * 1024)
        timer = QTimer(self)
        timer.setSingleShot(True)
        state = {"buffer": bytearray(), "error": "", "callback": callback, "timer": timer}
        self._pending[reply] = state
        reply.readyRead.connect(lambda: self._read(reply))
        reply.sslErrors.connect(lambda errors: self._ssl_error(reply))
        reply.finished.connect(lambda: self._finished(reply))
        timer.timeout.connect(lambda: self._timeout(reply))
        timer.start(REQUEST_TIMEOUT_MS)
        return reply

    def _read(self, reply):
        state = self._pending.get(reply)
        if state is None:
            return
        remaining = MAX_RESPONSE_BYTES - len(state["buffer"])
        chunk = bytes(reply.read(remaining + 1))
        if len(chunk) > remaining:
            state["error"] = "The server response is too large. Contact your server administrator."
            reply.abort()
            return
        state["buffer"].extend(chunk)

    def _ssl_error(self, reply):
        state = self._pending.get(reply)
        if state:
            state["error"] = "The server certificate could not be verified. Ask your administrator to install a trusted certificate."
            # HTTPS always verifies certificates, even when LAN HTTP is enabled.
            reply.abort()

    def _timeout(self, reply):
        state = self._pending.get(reply)
        if state:
            state["error"] = "The server did not respond within 10 seconds. Try again."
            reply.abort()

    def _finished(self, reply):
        self._read(reply)
        state = self._pending.pop(reply, None)
        if state is None:
            reply.deleteLater()
            return
        state["timer"].stop()
        state["timer"].deleteLater()
        status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute) or 0
        error = state["error"]
        data = None
        if not error and 300 <= status < 400:
            error = "The server redirected this request. Enter its final address and try again."
        if not error and state["buffer"]:
            try:
                data = json.loads(state["buffer"].decode("utf-8"))
            except (ValueError, UnicodeError):
                error = "The server returned an invalid response. Check the Work Chat server address."
        if not error and not 200 <= status < 300:
            detail = data.get("detail", data.get("error", "")) if isinstance(data, dict) else ""
            if isinstance(detail, dict):
                detail = detail.get("message", "")
            if isinstance(detail, str) and detail:
                error = detail[:500]
            elif status == 401:
                error = "Your session has ended. Sign in again."
            else:
                error = "Could not reach the office chat server. Check the address and your office network connection."
        callback = state["callback"]
        reply.deleteLater()
        if callback is not None:
            callback(data, error, status)

    def cancel(self, reply):
        state = self._pending.get(reply)
        if state:
            state["callback"] = None
            reply.abort()

    def cancel_all(self, except_reply=None):
        for reply in list(self._pending):
            if reply is not except_reply:
                self.cancel(reply)


def _positive_id(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


class WorkChatWindow(QDialog):
    """Sign in to the office server, then use Team Room or direct messages."""

    session_changed = Signal(str, str, object)

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.api = _ApiClient(self)
        self.token = ""
        self.user = None
        self._session_generation = 0
        self._conversation_generation = 0
        self._auth_busy = False
        self._users_pending = False
        self._messages_pending = False
        self._send_pending = False
        self._message_reply = None
        self._send_reply = None
        self._peer_id = None
        self._messages = {}
        self._cursor = None
        self._drafts = {}
        self._poll_ticks = 0
        self._shutting_down = False
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(2000)
        self.poll_timer.timeout.connect(self._poll)
        self.setWindowTitle("Jeffery Work Chat")
        self.resize(840, 630)
        self.setMinimumSize(650, 500)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        title = QLabel("Work Chat")
        title.setStyleSheet("font-size: 23px; font-weight: 600;")
        layout.addWidget(title)
        intro = QLabel("Talk with coworkers on your office server. Talk to Jeffery is your separate Groq AI chat.")
        intro.setObjectName("hint")
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.PlainText)
        layout.addWidget(intro)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        self._build_auth()
        self._build_chat()
        self.status = QLabel("Enter the main computer's office IP address. HTTP office connections are unencrypted.")
        self.status.setObjectName("hint")
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        self.status.setAccessibleName("Work Chat connection status")
        layout.addWidget(self.status)
        self.configure(settings)

    def _build_auth(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 20, 0, 0)
        form = QFormLayout()
        form.setSpacing(12)
        self.server = QLineEdit()
        self.server.setMaxLength(2048)
        self.server.setPlaceholderText("192.168.50.194")
        self.server.setAccessibleName("Office Work Chat server address")
        self.username = QLineEdit()
        self.username.setMaxLength(32)
        self.username.setPlaceholderText("Your username")
        self.username.setAccessibleName("Work Chat username")
        self.password = QLineEdit()
        self.password.setMaxLength(128)
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setAccessibleName("Work Chat password")
        self.confirm_password = QLineEdit()
        self.confirm_password.setMaxLength(128)
        self.confirm_password.setEchoMode(QLineEdit.Password)
        self.confirm_password.setAccessibleName("Confirm Work Chat password")
        for name, widget in (("Server IP or address", self.server), ("Username", self.username), ("Password", self.password)):
            label = QLabel(name)
            label.setBuddy(widget)
            form.addRow(label, widget)
        self.confirm_label = QLabel("Confirm password")
        self.confirm_label.setBuddy(self.confirm_password)
        form.addRow(self.confirm_label, self.confirm_password)
        layout.addLayout(form)
        self.signup = QCheckBox("Create a new account")
        self.signup.toggled.connect(self._set_signup)
        layout.addWidget(self.signup)
        self.auth_help = QLabel("Usernames: 3–32 letters, numbers, underscores, dots or dashes. Passwords: 12–128 characters.")
        self.auth_help.setWordWrap(True)
        self.auth_help.setObjectName("hint")
        layout.addWidget(self.auth_help)
        self.auth_button = QPushButton("Sign In")
        self.auth_button.setObjectName("primary")
        self.auth_button.clicked.connect(self.authenticate)
        self.password.returnPressed.connect(self.authenticate)
        self.confirm_password.returnPressed.connect(self.authenticate)
        layout.addWidget(self.auth_button, 0, Qt.AlignRight)
        layout.addStretch(1)
        self.pages.addWidget(page)
        self._set_signup(False)

    def _build_chat(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        top = QHBoxLayout()
        self.account_label = QLabel("")
        self.account_label.setTextFormat(Qt.PlainText)
        self.account_label.setObjectName("hint")
        top.addWidget(self.account_label, 1)
        self.refresh_button = QPushButton("Load latest")
        self.refresh_button.setToolTip("Refresh coworkers and load the latest 50 messages in this conversation")
        self.refresh_button.clicked.connect(self.load_latest)
        top.addWidget(self.refresh_button)
        self.signout_button = QPushButton("Sign out")
        self.signout_button.clicked.connect(self.sign_out)
        top.addWidget(self.signout_button)
        layout.addLayout(top)
        splitter = QSplitter()
        sidebar = QWidget()
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 8, 0)
        sidebar_layout.addWidget(QLabel("Conversations"))
        self.conversations = QListWidget()
        self.conversations.setMinimumWidth(165)
        self.conversations.setAccessibleName("Team Room and coworkers")
        self.conversations.currentItemChanged.connect(self._conversation_changed)
        sidebar_layout.addWidget(self.conversations, 1)
        splitter.addWidget(sidebar)
        conversation = QWidget()
        conversation_layout = QVBoxLayout(conversation)
        conversation_layout.setContentsMargins(0, 0, 0, 0)
        self.conversation_title = QLabel("Team Room")
        self.conversation_title.setTextFormat(Qt.PlainText)
        self.conversation_title.setStyleSheet("font-size: 17px; font-weight: 600;")
        conversation_layout.addWidget(self.conversation_title)
        self.transcript = QPlainTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setPlaceholderText("No messages yet. Start the conversation.")
        self.transcript.setAccessibleName("Work Chat messages")
        conversation_layout.addWidget(self.transcript, 1)
        self.composer = QPlainTextEdit()
        self.composer.setPlaceholderText("Write to your coworkers… (Ctrl+Enter to send)")
        self.composer.setAccessibleName("Work Chat message to send")
        self.composer.setMaximumHeight(100)
        self.composer.textChanged.connect(self._update_send)
        conversation_layout.addWidget(self.composer)
        bottom = QHBoxLayout()
        self.counter = QLabel("0 / 4000")
        self.counter.setObjectName("hint")
        bottom.addWidget(self.counter, 1)
        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("primary")
        self.send_button.clicked.connect(self.send_message)
        bottom.addWidget(self.send_button)
        conversation_layout.addLayout(bottom)
        splitter.addWidget(conversation)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([190, 590])
        layout.addWidget(splitter, 1)
        self.send_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self.composer)
        self.send_shortcut.activated.connect(self.send_message)
        self.keypad_shortcut = QShortcut(QKeySequence("Ctrl+Enter"), self.composer)
        self.keypad_shortcut.activated.connect(self.send_message)
        self.pages.addWidget(page)
        self._update_send()

    def configure(self, settings=None):
        if settings is not None:
            self.settings = settings
        colors = palette(self.settings)
        self.setStyleSheet(notes_style(self.settings) + f"\nQSplitter::handle {{ background: {colors['border']}; }}")
        if not self.token and not self._auth_busy:
            values = getattr(self.settings, "values", self.settings)
            self.server.setText(values.get("work_chat_server", ""))
            self.username.setText(values.get("work_chat_username", ""))

    def _set_signup(self, enabled):
        self.confirm_password.setVisible(enabled)
        self.confirm_label.setVisible(enabled)
        self.confirm_password.clear()
        self.auth_button.setText("Create Account" if enabled else "Sign In")

    def _set_auth_busy(self, busy):
        self._auth_busy = busy
        for widget in (self.server, self.username, self.password, self.confirm_password,
                       self.signup, self.auth_button):
            widget.setEnabled(not busy)

    def authenticate(self):
        if self._auth_busy or self.token or self._shutting_down:
            return
        try:
            server = normalize_server_url(self.server.text())
        except ValueError as exc:
            self.status.setText(str(exc))
            self.server.setFocus()
            return
        username, password = self.username.text().strip(), self.password.text()
        if not USERNAME_RE.fullmatch(username):
            self.status.setText("Use a username of 3–32 letters, numbers, underscores, dots or dashes.")
            self.username.setFocus()
            return
        if not 12 <= len(password) <= 128:
            self.status.setText("Use a password between 12 and 128 characters.")
            self.password.setFocus()
            return
        if self.signup.isChecked() and password != self.confirm_password.text():
            self.status.setText("The passwords do not match.")
            self.confirm_password.setFocus()
            return
        try:
            self.settings.save({"work_chat_server": server, "work_chat_username": username})
        except OSError:
            self.status.setText("Could not save the server address. Check that your settings folder is writable.")
            return
        self.server.setText(server)
        self.username.setText(username)
        self._session_generation += 1
        generation = self._session_generation
        self.api.cancel_all()
        self.api.base_url = server
        self._set_auth_busy(True)
        self.status.setText("Creating your account…" if self.signup.isChecked() else "Signing in…")
        path = "/api/auth/register" if self.signup.isChecked() else "/api/auth/login"

        def received(data, error, status):
            if generation != self._session_generation or self._shutting_down:
                return
            self._set_auth_busy(False)
            self.password.clear()
            self.confirm_password.clear()
            if error:
                self.status.setText(error)
                self.password.setFocus()
                return
            token = data.get("token") if isinstance(data, dict) else None
            user = data.get("user") if isinstance(data, dict) else None
            if (not isinstance(token, str) or not token or len(token) > 4096
                    or not token.isascii() or any(not 33 <= ord(char) <= 126 for char in token)
                    or not isinstance(user, dict) or not _positive_id(user.get("id"))
                    or not isinstance(user.get("username"), str)
                    or not USERNAME_RE.fullmatch(user["username"])):
                self.status.setText("The server returned an invalid sign-in response.")
                return
            self.token, self.user = token, user
            self.session_changed.emit(self.api.base_url, token, user)
            self.account_label.setText(f"Signed in as {user['username']}")
            self.pages.setCurrentIndex(1)
            self._peer_id = None
            self._drafts.clear()
            self._reset_messages()
            self._populate_users([])
            self._update_send()
            self.status.setText("Connected to your office server.")
            if self.isVisible():
                self.poll_timer.start()
                self.refresh_users()
                self.refresh_messages()

        self.api.request("POST", path, {"username": username, "password": password}, received)
        self.password.clear()
        self.confirm_password.clear()

    def _session_error(self, error, status):
        if status == 401:
            self._clear_session()
            self.status.setText("Your session has ended. Sign in again.")
            return True
        if error:
            self.status.setText(error)
            return True
        return False

    def refresh_users(self):
        if not self.token or self._users_pending or not self.isVisible():
            return
        self._users_pending = True
        generation = self._session_generation

        def received(data, error, status):
            if generation != self._session_generation:
                return
            self._users_pending = False
            if self._session_error(error, status):
                return
            users = data.get("users") if isinstance(data, dict) else None
            if not isinstance(users, list):
                self.status.setText("The server returned an invalid coworker list.")
                return
            self._populate_users(users[:2000])

        self.api.request("GET", "/api/users", None, received, self.token)

    def _populate_users(self, users):
        with QSignalBlocker(self.conversations):
            self.conversations.clear()
            room = QListWidgetItem("# Team Room")
            room.setData(Qt.UserRole, None)
            self.conversations.addItem(room)
            selected = room
            seen = set()
            for user in users:
                if (not isinstance(user, dict) or not _positive_id(user.get("id"))
                        or not isinstance(user.get("username"), str)
                        or not USERNAME_RE.fullmatch(user["username"])
                        or user["id"] in seen or (self.user and user["id"] == self.user["id"])):
                    continue
                seen.add(user["id"])
                item = QListWidgetItem(user["username"])
                item.setData(Qt.UserRole, user["id"])
                self.conversations.addItem(item)
                if user["id"] == self._peer_id:
                    selected = item
            self.conversations.setCurrentItem(selected)
        if selected.data(Qt.UserRole) != self._peer_id:
            self._conversation_changed(selected, None)

    def _conversation_changed(self, current, previous):
        if current is None:
            return
        peer_id = current.data(Qt.UserRole)
        if peer_id == self._peer_id:
            return
        self._drafts[self._peer_id] = self.composer.toPlainText()
        self._peer_id = peer_id
        self._conversation_generation += 1
        self.api.cancel(self._message_reply)
        self._messages_pending = False
        self._message_reply = None
        self.conversation_title.setText("Team Room" if peer_id is None else f"Message {current.text()}")
        self.composer.setPlainText(self._drafts.get(peer_id, ""))
        self._reset_messages()
        self._update_send()
        self.status.setText("Loading conversation…")
        self.refresh_messages()

    def _reset_messages(self):
        self._messages.clear()
        self._cursor = None
        self.transcript.clear()

    def refresh_messages(self, burst=0):
        if not self.token or self._messages_pending or not self.isVisible():
            return
        self._messages_pending = True
        session, conversation = self._session_generation, self._conversation_generation
        query = {"limit": MESSAGE_BATCH_LIMIT}
        if self._peer_id is not None:
            query["peer_id"] = self._peer_id
        if self._cursor is not None:
            query["after_id"] = self._cursor
        before_cursor = self._cursor

        def received(data, error, status):
            if session != self._session_generation or conversation != self._conversation_generation:
                return
            self._messages_pending = False
            self._message_reply = None
            if self._session_error(error, status):
                return
            messages = data.get("messages") if isinstance(data, dict) else None
            if not isinstance(messages, list) or len(messages) > MESSAGE_BATCH_LIMIT:
                self.status.setText("The server returned an invalid message list.")
                return
            if not all(self._valid_message(message) for message in messages):
                self.status.setText("The server returned an invalid message.")
                return
            self._merge_messages(messages)
            more = data.get("has_more") is True
            if more and self._cursor != before_cursor and burst < 4:
                QTimer.singleShot(0, lambda: self._continue_refresh(session, conversation, burst + 1))
            else:
                self.status.setText("Catching up on messages…" if more else "Connected · messages refresh every 2 seconds.")

        self._message_reply = self.api.request("GET", "/api/messages?" + urlencode(query), None, received, self.token)

    def _continue_refresh(self, session, conversation, burst):
        if session == self._session_generation and conversation == self._conversation_generation:
            self.refresh_messages(burst)

    def _valid_message(self, message, peer_id=_CURRENT_CONVERSATION):
        if (not isinstance(message, dict) or not _positive_id(message.get("id"))
                or not _positive_id(message.get("sender_id"))
                or not isinstance(message.get("sender_username"), str)
                or not USERNAME_RE.fullmatch(message["sender_username"])
                or not isinstance(message.get("body"), str) or not 1 <= len(message["body"]) <= 4000
                or not isinstance(message.get("created_at"), str) or len(message["created_at"]) > 80):
            return False
        recipient = message.get("recipient_id")
        if peer_id is _CURRENT_CONVERSATION:
            peer_id = self._peer_id
        if peer_id is None:
            return recipient is None
        own_id = self.user["id"] if self.user else None
        return ((message["sender_id"] == own_id and recipient == peer_id)
                or (message["sender_id"] == peer_id and recipient == own_id))

    def _merge_messages(self, messages):
        changed = False
        for message in messages:
            identifier = message["id"]
            if identifier not in self._messages:
                self._messages[identifier] = message
                changed = True
            self._cursor = max(identifier, self._cursor or 0)
        if len(self._messages) > MAX_VISIBLE_MESSAGES:
            for identifier in sorted(self._messages)[:-MAX_VISIBLE_MESSAGES]:
                del self._messages[identifier]
        if changed:
            scroll = self.transcript.verticalScrollBar()
            at_bottom = scroll.value() >= scroll.maximum() - 8
            previous_position = scroll.value()
            entries = []
            for identifier in sorted(self._messages):
                message = self._messages[identifier]
                stamp = message["created_at"]
                try:
                    stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone().strftime("%b %d, %I:%M %p")
                except (ValueError, OverflowError, OSError):
                    stamp = ""
                entries.append(f"{message['sender_username']}  ·  {stamp}\n{message['body']}")
            self.transcript.setPlainText("\n\n".join(entries))
            scroll.setValue(scroll.maximum() if at_bottom else previous_position)

    def _update_send(self):
        size = len(self.composer.toPlainText())
        self.counter.setText(f"{size} / 4000")
        self.send_button.setEnabled(bool(self.token) and not self._send_pending
                                    and 1 <= size <= 4000 and bool(self.composer.toPlainText().strip()))
        self.refresh_button.setEnabled(not self._send_pending)

    def send_message(self):
        body = self.composer.toPlainText()
        if not self.token or self._send_pending or not body.strip() or not 1 <= len(body) <= 4000:
            return
        session, conversation = self._session_generation, self._conversation_generation
        peer_id = self._peer_id
        destination = self.conversation_title.text()
        self._send_pending = True
        self._update_send()
        self.status.setText("Sending…")

        def received(data, error, status):
            if session != self._session_generation:
                return
            self._send_pending = False
            self._send_reply = None
            self._update_send()
            if self._session_error(error, status):
                if status != 401 and conversation != self._conversation_generation:
                    self.status.setText(f"Could not send to {destination}. Your draft was kept. {error}")
                return
            message = data.get("message") if isinstance(data, dict) else None
            if (not self._valid_message(message, peer_id)
                    or message["sender_id"] != self.user["id"] or message["body"] != body):
                self.status.setText("The server returned an invalid send response. Load latest before retrying.")
                return
            # Poll cursors advance only from GET responses: a send response may
            # overtake an older in-flight GET and must not skip coworkers' posts.
            if conversation == self._conversation_generation:
                cursor = self._cursor
                self._merge_messages([message])
                self._cursor = cursor
            if self._drafts.get(peer_id) == body:
                self._drafts.pop(peer_id, None)
            if self._peer_id == peer_id and self.composer.toPlainText() == body:
                self.composer.clear()
            self.status.setText("Message sent." if conversation == self._conversation_generation else f"Message sent to {destination}.")
            self.refresh_messages()

        self._send_reply = self.api.request("POST", "/api/messages",
            {"body": body, "recipient_id": peer_id}, received, self.token)

    def load_latest(self):
        if not self.token or self._send_pending:
            return
        self._conversation_generation += 1
        self.api.cancel(self._message_reply)
        self.api.cancel(self._send_reply)
        self._message_reply = self._send_reply = None
        self._messages_pending = self._send_pending = False
        self._reset_messages()
        self._update_send()
        self.refresh_users()
        self.refresh_messages()

    def _poll(self):
        if not self.token or not self.isVisible():
            self.poll_timer.stop()
            return
        self._poll_ticks += 1
        if self._poll_ticks % 5 == 0:
            self.refresh_users()
        self.refresh_messages()

    def _clear_session(self):
        self._session_generation += 1
        self._conversation_generation += 1
        self.poll_timer.stop()
        self.api.cancel_all()
        self.token = ""
        self.user = None
        self.session_changed.emit(self.api.base_url, "", None)
        self._users_pending = self._messages_pending = self._send_pending = False
        self._message_reply = self._send_reply = None
        self._drafts.clear()
        self._peer_id = None
        self._reset_messages()
        self.composer.clear()
        self.password.clear()
        self.confirm_password.clear()
        self.account_label.clear()
        with QSignalBlocker(self.conversations):
            self.conversations.clear()
        self.conversation_title.setText("Team Room")
        self.pages.setCurrentIndex(0)
        self._set_auth_busy(False)
        self._update_send()

    def sign_out(self):
        token = self.token
        self._clear_session()
        self.status.setText("Signed out. Your password and messages are not saved on this computer.")
        if token:
            self.api.request("POST", "/api/auth/logout", {}, lambda data, error, status: None, token)

    def showEvent(self, event):
        super().showEvent(event)
        if self.token and not self._shutting_down:
            self.poll_timer.start()
            self.refresh_users()
            self.refresh_messages()

    def hideEvent(self, event):
        self.poll_timer.stop()
        if self._auth_busy:
            self._session_generation += 1
        self._conversation_generation += 1
        # A posted message may already be committed. Let it finish so its
        # matching draft can be cleared rather than inviting a duplicate send.
        self.api.cancel_all(except_reply=self._send_reply)
        self._users_pending = self._messages_pending = False
        self._message_reply = None
        self.password.clear()
        self.confirm_password.clear()
        self._set_auth_busy(False)
        self._update_send()
        super().hideEvent(event)

    def shutdown(self):
        self._shutting_down = True
        self._clear_session()
        self.api.cancel_all()
        self.hide()
