"""Validated, atomic settings and an ordinary user-owned Windows startup entry."""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from config import DEFAULTS, ROOT, data_directory


class AppSettings:
    def __init__(self, path: Path | None = None):
        self.path = path or data_directory() / "settings.json"
        self.values = copy.deepcopy(DEFAULTS)
        self.warning = ""
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("Expected an object")
                self.values.update(self.validate(raw))
                if raw.get("pet_name") == "Pip":
                    self.values["pet_name"] = "Jeffery"
            except (OSError, ValueError, TypeError):
                self.warning = "Settings could not be read; safe defaults are in use."
                try:
                    self.path.replace(self.path.with_suffix(".corrupt.json"))
                except OSError:
                    pass

    @staticmethod
    def validate(raw: dict) -> dict:
        result = {}
        ranges = {"opacity": (35, 100), "interval_ms": (500, 5000), "pet_size": (48, 160),
                  "speed": (5, 150), "speech_frequency": (30, 1800),
                  "cpu_alert": (40, 100), "ram_alert": (40, 100),
                  "alert_duration": (1, 60), "alert_cooldown": (30, 900), "focus_minutes": (1, 120),
                  "note_repeat_minutes": (0, 1440), "hide_seconds": (3, 120),
                  "ai_interval_seconds": (60, 1800)}
        options = {"theme": ("midnight", "forest", "plum"), "pet_palette": ("mint", "sky", "amber", "rose"),
                   "roaming_mode": ("bottom", "free"), "character": ("robot", "cat", "knight"),
                   "mouse_mode": ("off", "watch", "chase", "shy")}
        for key, default in DEFAULTS.items():
            value = raw.get(key, default)
            if isinstance(default, bool):
                result[key] = value if isinstance(value, bool) else default
            elif key in ranges:
                low, high = ranges[key]
                result[key] = max(low, min(high, value)) if type(value) is int else default
            elif key.endswith("_position"):
                result[key] = value if isinstance(value, list) and len(value) == 2 and all(type(v) is int and abs(v) < 100000 for v in value) else None
            elif key in options:
                result[key] = value if value in options[key] else default
            elif key == "pet_name":
                cleaned = "".join(ch for ch in value if ch.isprintable()).strip()[:20] if isinstance(value, str) else ""
                result[key] = cleaned or default
            elif key == "business_name":
                result[key] = "".join(c for c in value if c.isprintable()).strip()[:80] if isinstance(value, str) else ""
            elif key == "ai_model":
                cleaned = value.strip() if isinstance(value, str) else ""
                result[key] = cleaned if 0 < len(cleaned) <= 100 and all(c.isalnum() or c in '/-._' for c in cleaned) else default
        return result

    def __getitem__(self, key):
        return self.values[key]

    def save(self, changes: dict | None = None):
        updated = dict(self.values)
        if changes:
            updated.update(changes)
        updated = self.validate(updated)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)
        self.values = updated


def set_windows_startup(enabled: bool):
    if sys.platform != "win32":
        if enabled:
            raise OSError("Start with Windows is only available on Windows.")
        return
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
        if enabled:
            if getattr(sys, "frozen", False):
                arguments = [sys.executable]
            else:
                executable = Path(sys.executable)
                windowed = executable.with_name("pythonw.exe")
                arguments = [str(windowed if windowed.exists() else executable), str(ROOT / "main.py")]
            winreg.SetValueEx(key, "PixelSystemBuddy", 0, winreg.REG_SZ, subprocess.list2cmdline(arguments))
        else:
            try:
                winreg.DeleteValue(key, "PixelSystemBuddy")
            except FileNotFoundError:
                pass
