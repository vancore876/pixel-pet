"""Bounded asynchronous Windows UI Automation, independent of the GUI thread."""
from __future__ import annotations
import json
import os
import sys
import ctypes
from pathlib import Path
from PySide6.QtCore import QObject, QTimer, QProcess, QRect, Signal
from PySide6.QtWidgets import QApplication
from config import ROOT


def checked_rect(value):
    if not isinstance(value, list) or len(value) != 4 or any(type(v) is not int or abs(v) > 100000 for v in value):
        raise ValueError("Invalid desktop coordinates.")
    if not 0 < value[2] <= 50000 or not 0 < value[3] <= 50000:
        raise ValueError("Invalid desktop target size.")
    return value


def validate_targets(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("targets"), list):
        raise ValueError("Windows returned an incomplete desktop scan.")
    targets, seen = [], set()
    for raw in payload["targets"][:100]:
        try:
            if not isinstance(raw, dict) or raw.get("kind") not in ("folder", "tab", "window"):
                continue
            identifier = raw["id"]
            if not isinstance(identifier, str) or not identifier or len(identifier) > 160 or identifier in seen:
                continue
            if type(raw["hwnd"]) is not int or raw["hwnd"] <= 0 or type(raw["pid"]) is not int or raw["pid"] <= 0:
                continue
            target = {"id": identifier, "kind": raw["kind"], "rect": checked_rect(raw["rect"]),
                      "name": str(raw.get("name", ""))[:300], "path": str(raw.get("path", ""))[:1000],
                      "hwnd": raw["hwnd"], "pid": raw["pid"], "runtime": str(raw.get("runtime", ""))[:160]}
            if raw.get('window_rect') is not None:
                target['window_rect'] = checked_rect(raw['window_rect'])
            targets.append(target)
            seen.add(identifier)
        except (KeyError, ValueError, TypeError):
            continue
    return targets


def logical_rect(value, monitors, screens):
    """UIA uses physical pixels. Match the physical monitor to Qt's logical one."""
    x, y, width, height = checked_rect(value)
    center = (x + width / 2, y + height / 2)
    for monitor in monitors:
        try:
            mx, my, mw, mh = checked_rect(monitor["rect"])
            if not (mx <= center[0] < mx + mw and my <= center[1] < my + mh):
                continue
            screen = next((s for s in screens if s.name().casefold() == str(monitor["name"]).casefold()), None)
            if screen is None:
                continue
            geometry = screen.geometry()
            ratio = screen.devicePixelRatio()
            return QRect(round(geometry.x() + (x - mx) / ratio), round(geometry.y() + (y - my) / ratio),
                         max(1, round(width / ratio)), max(1, round(height / ratio)))
        except (KeyError, ValueError, TypeError):
            continue
    # At 100% scale these are identical; avoid inventing a scaling offset.
    return QRect(x, y, width, height)


class DesktopBridge(QObject):
    changed = Signal()
    status_changed = Signal(str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.targets, self.monitors = [], []
        self.status = "Windows desktop integration is available on Windows."
        self.supported = sys.platform == "win32"
        self.process = None
        self.buffer, self.ready = b"", False
        self.jobs, self.active = [], None
        self.counter = 0
        self.stopped = False
        self.scan_pending = False
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(self.timed_out)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.scan)
        self.configure()
        if self.supported:
            QTimer.singleShot(750, self.scan)

    def configure(self):
        if self.supported and self.settings["desktop_enabled"] and not self.stopped:
            self.timer.start(20000 if self.settings["low_power"] else 8000)
        else:
            self.timer.stop()
            self.targets = []
            self.changed.emit()

    def set_status(self, value):
        self.status = value
        self.status_changed.emit(value)

    def launch(self):
        if self.process is not None:
            return
        process = QProcess(self)
        self.process, self.ready, self.buffer = process, False, b""
        program = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        assets = Path(getattr(sys, "_MEIPASS", ROOT)) / "assets/windows_desktop.ps1"
        process.setProgram(str(program))
        process.setArguments(["-NoLogo", "-NoProfile", "-NonInteractive", "-STA", "-ExecutionPolicy", "Bypass", "-File", str(assets), "-OwnerPid", str(os.getpid())])
        process.readyReadStandardOutput.connect(self.read_output)
        process.readyReadStandardError.connect(lambda: process.readAllStandardError())
        process.finished.connect(lambda *args: self.exited(process))
        process.errorOccurred.connect(lambda *args: self.process_error(process))
        process.start()
        self.timeout.start(15000)

    def request(self, action, payload, callback):
        if not self.supported or not self.settings["desktop_enabled"] or self.stopped:
            callback(None, "Enable real desktop play on Windows first.")
            return False
        if action not in ("scan", "capture_text", "select_tab", "replace_text") or len(self.jobs) >= 4:
            callback(None, "Desktop play is busy. Try again in a moment.")
            return False
        self.counter += 1
        job = ({**payload, "action": action, "id": self.counter}, callback)
        self.jobs.append(job)
        self.launch()
        self.pump()
        return True

    def pump(self):
        if not self.ready or self.active or not self.jobs or self.process is None:
            return
        self.active = self.jobs.pop(0)
        if sys.platform == 'win32' and self.active[0]['action'] in ('select_tab', 'replace_text'):
            # The user clicked in our app; grant only this helper foreground access.
            ctypes.windll.user32.AllowSetForegroundWindow(int(self.process.processId()))
        self.process.write((json.dumps(self.active[0], ensure_ascii=False) + "\n").encode("utf-8"))
        self.timeout.start(10000)

    def read_output(self):
        if self.process is None:
            return
        self.buffer += bytes(self.process.readAllStandardOutput())
        if len(self.buffer) > 1024 * 1024:
            self.kill()
            self.fail_all("The Windows scan was too large. Close extra windows and refresh.")
            return
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            try:
                value = json.loads(line.decode("utf-8-sig"))
            except (ValueError, UnicodeError):
                continue
            self.handle_response(value)

    def handle_response(self, value):
        if not isinstance(value, dict):
            return
        if value.get("ready") is True:
            self.ready = True
            self.timeout.stop()
            self.pump()
        elif self.active and value.get("id") == self.active[0]["id"]:
            _, callback = self.active
            self.active = None
            self.timeout.stop()
            callback(value.get("result") if value.get("ok") is True else None,
                     "" if value.get("ok") is True else str(value.get("error", "Windows desktop action failed."))[:300])
            self.pump()

    def scan(self):
        if self.scan_pending or self.stopped or not self.settings["desktop_enabled"]:
            return
        self.scan_pending = True
        self.request("scan", {}, self.scanned)

    def scanned(self, result, error):
        self.scan_pending = False
        if error:
            self.set_status(error)
            self.targets = []
            self.changed.emit()
            return
        try:
            self.targets = validate_targets(result)
            self.monitors = result.get("monitors", [])[:16]
            self.set_status(f"{len(self.targets)} visible targets · folders, tabs, and window edges")
        except (ValueError, TypeError):
            self.targets = []
            self.set_status("Windows returned an incomplete desktop scan.")
        self.changed.emit()

    def target(self, identifier):
        return next((t for t in self.targets if t["id"] == identifier), None)

    def rect(self, target):
        return logical_rect(target["rect"], self.monitors, QApplication.screens())

    def fail_all(self, message):
        self.timeout.stop()
        jobs = ([self.active] if self.active else []) + self.jobs
        self.active, self.jobs = None, []
        for _, callback in jobs:
            callback(None, message)
        self.set_status(message)

    def kill(self):
        process, self.process = self.process, None
        self.ready = False
        if process is not None:
            process.kill()

    def process_error(self, process):
        if process is self.process:
            self.kill()
            self.fail_all("Windows desktop access could not start. Check that Windows PowerShell is available.")

    def exited(self, process):
        if process is self.process:
            self.process = None
            self.ready = False
            self.fail_all("Windows desktop access stopped. Refresh to reconnect.")
        process.deleteLater()

    def timed_out(self):
        self.kill()
        self.fail_all("A Windows app did not respond to the desktop scan. Refresh to try again.")

    def stop(self):
        self.stopped = True
        self.timer.stop()
        self.fail_all("Desktop play stopped.")
        self.kill()
