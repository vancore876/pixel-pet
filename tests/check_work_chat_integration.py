"""Real Qt/API integration with a disposable local server and database.

Run with the desktop interpreter: python tests/check_work_chat_integration.py
Set WORK_CHAT_SERVER_PYTHON to an interpreter with requirements-server.txt;
otherwise .server-venv is used. No existing account or app data is touched.
Set WORK_CHAT_TEST_LAN_IP to this PC's private IPv4 address to test LAN HTTP
and bare-IP entry instead of the default loopback connection.
"""
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import URLError
from urllib.request import urlopen

import psutil

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from settings import AppSettings
from work_chat import WorkChatWindow, _ApiClient

APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)
SERVER_PYTHON = Path(os.environ.get("WORK_CHAT_SERVER_PYTHON", str(ROOT / ".server-venv" /
    ("Scripts/python.exe" if os.name == "nt" else "bin/python"))))
LAN_IP = os.environ.get("WORK_CHAT_TEST_LAN_IP", "")
if LAN_IP:
    lan_address = ipaddress.IPv4Address(LAN_IP)
    if not any(lan_address in ipaddress.IPv4Network(network) for network in
               ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")):
        raise ValueError("WORK_CHAT_TEST_LAN_IP must be this PC's private IPv4 address.")


def wait_for(predicate, seconds=10):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        APP.processEvents()
        time.sleep(0.01)
    if not predicate():
        raise AssertionError("Timed out waiting for the disposable Work Chat server.")


def stop_server_process(process, known_children=()):
    """Stop this test's descendants before the Windows venv redirector.

    Windows may launch a second Python process behind a virtualenv executable.
    Stopping only Popen's PID can leave that server holding the SQLite file.
    Process objects are captured from this exact parent, never a global name
    match, so another running Jeffery or work server cannot be stopped here.
    """
    children = {child.pid: child for child in known_children}
    try:
        children.update({child.pid: child for child in psutil.Process(process.pid).children(recursive=True)})
    except psutil.NoSuchProcess:
        pass
    descendants = list(reversed(list(children.values())))
    for child in descendants:
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(descendants, timeout=5)
    for child in alive:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(alive, timeout=5)
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    detail = ""
    if process.stderr and not process.stderr.closed:
        try:
            detail = process.stderr.read(8192).decode("utf-8", "replace")
        finally:
            process.stderr.close()
    return detail


@unittest.skipUnless(SERVER_PYTHON.is_file(), "Optional server interpreter is not installed")
class RealServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="jeffery-chat-test-")
        cls.process = None
        cls.children = {}
        # Class cleanups also run when setUpClass fails or is interrupted.
        cls.addClassCleanup(cls.cleanup_server)
        host = LAN_IP or "127.0.0.1"
        with socket.socket() as probe:
            probe.bind((host, 0))
            port = probe.getsockname()[1]
        cls.origin = f"http://{host}:{port}"
        cls.server_entry = f"{host}:{port}" if LAN_IP else cls.origin
        lan_arguments = ["--host", host, "--allow-lan-http"] if LAN_IP else []
        cls.process = subprocess.Popen([str(SERVER_PYTHON), "-m", "work_server", "--port", str(port),
            "--database", str(Path(cls.temp.name) / "chat.sqlite"), *lan_arguments], cwd=ROOT,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            try:
                cls.children.update({child.pid: child for child in psutil.Process(cls.process.pid).children(recursive=True)})
            except psutil.NoSuchProcess:
                pass
            if cls.process.poll() is not None:
                detail = stop_server_process(cls.process, cls.children.values())
                cls.process = None
                raise RuntimeError("Disposable server failed to start: " + detail)
            try:
                with urlopen(cls.origin + "/api/health", timeout=0.3) as response:
                    if json.load(response).get("service") == "Jeffery Work Chat":
                        return
            except (URLError, OSError):
                time.sleep(0.05)
        raise RuntimeError("Disposable server did not become ready.")

    @classmethod
    def cleanup_server(cls):
        if cls.process is not None:
            stop_server_process(cls.process, cls.children.values())
            cls.process = None
        cls.temp.cleanup()

    def setUp(self):
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            window.shutdown()
            window.deleteLater()
        APP.processEvents()

    def account(self, username):
        window = WorkChatWindow(AppSettings(Path(self.temp.name) / (username + ".json")))
        self.windows.append(window)
        window.show()
        window.server.setText(self.server_entry)
        window.username.setText(username)
        window.signup.setChecked(True)
        window.password.setText("disposable-password-123")
        window.confirm_password.setText("disposable-password-123")
        window.authenticate()
        wait_for(lambda: not window._auth_busy)
        self.assertTrue(window.token, window.status.text())
        self.assertEqual(window.api.base_url, self.origin)
        wait_for(lambda: not window._users_pending and not window._messages_pending)
        return window

    def transport(self, token, path):
        results = []
        client = _ApiClient()
        client.base_url = self.origin
        client.request("GET", path, None, lambda data, error, status: results.append((data, error, status)), token)
        wait_for(lambda: bool(results))
        client.deleteLater()
        return results[0]

    def choose(self, window, peer_id):
        window.refresh_users()
        wait_for(lambda: not window._users_pending)
        for index in range(window.conversations.count()):
            if window.conversations.item(index).data(Qt.UserRole) == peer_id:
                window.conversations.setCurrentRow(index)
                wait_for(lambda: not window._messages_pending)
                return
        self.fail("Coworker did not appear in the real server roster.")

    def test_two_accounts_team_direct_literal_text_conflict_and_logout(self):
        alice, bob = self.account("qa_alice"), self.account("qa_bob")
        team_text = "Office Team Room check: <b>literal text</b> & safe."
        alice.composer.setPlainText(team_text)
        alice.send_message()
        wait_for(lambda: not alice._send_pending)
        self.assertEqual(alice.composer.toPlainText(), "", alice.status.text())
        bob.refresh_messages()
        wait_for(lambda: not bob._messages_pending)
        self.assertIn(team_text, bob.transcript.toPlainText())

        self.choose(alice, bob.user["id"])
        self.choose(bob, alice.user["id"])
        direct_text = "Private coworker message <script>literal</script>"
        alice.composer.setPlainText(direct_text)
        alice.send_message()
        wait_for(lambda: not alice._send_pending)
        bob.refresh_messages()
        wait_for(lambda: not bob._messages_pending)
        self.assertIn(direct_text, bob.transcript.toPlainText())
        self.assertNotIn(team_text, bob.transcript.toPlainText())
        reply_text = "Reply from Bob: both directions work."
        bob.composer.setPlainText(reply_text)
        bob.send_message()
        wait_for(lambda: not bob._send_pending)
        self.assertEqual(bob.composer.toPlainText(), "", bob.status.text())
        # Alice receives the return message through normal polling, without
        # clicking Refresh or depending on the sender's POST response.
        wait_for(lambda: reply_text in alice.transcript.toPlainText())
        self.choose(bob, None)
        self.assertIn(team_text, bob.transcript.toPlainText())
        self.assertNotIn(direct_text, bob.transcript.toPlainText())

        conflict = WorkChatWindow(AppSettings(Path(self.temp.name) / "conflict.json"))
        self.windows.append(conflict)
        conflict.show()
        conflict.server.setText(self.server_entry)
        conflict.username.setText("qa_alice")
        conflict.signup.setChecked(True)
        conflict.password.setText("disposable-password-123")
        conflict.confirm_password.setText("disposable-password-123")
        conflict.authenticate()
        wait_for(lambda: not conflict._auth_busy)
        self.assertFalse(conflict.token)
        self.assertIn("username", conflict.status.text().lower())
        self.assertNotIn("Could not reach", conflict.status.text())

        old_token = bob.token
        bob.sign_out()
        wait_for(lambda: not bob.api._pending)
        self.assertEqual(bob.token, "")
        self.assertEqual(self.transport(old_token, "/api/me")[2], 401)
        for path in Path(self.temp.name).glob("*.json"):
            self.assertNotIn("disposable-password-123", path.read_text())
            self.assertNotIn(old_token, path.read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
