"""Paths, defaults, and screen-boundary helpers."""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "PixelSystem Buddy"
APP_VERSION = "6.0"
ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
ASSETS = Path(getattr(sys, "_MEIPASS", ROOT)) / "assets" / "pet"
DEFAULTS = {
    "always_on_top": True, "start_with_windows": False, "launch_minimized": False,
    "show_cpu": True, "show_ram": True, "show_disk": True, "show_network": True,
    "show_gpu": True, "opacity": 88, "interval_ms": 1000, "compact": False,
    "graphs": True, "click_through": False, "overlay_visible": True,
    "pet_enabled": True, "pet_size": 96, "speed": 38, "speech": True,
    "speech_frequency": 180, "follow_active_monitor": False, "reactions": True,
    "follow_mouse": False, "overlay_position": None, "pet_position": None,
    "theme": "midnight", "pet_palette": "mint", "pet_name": "Jeffery",
    "roaming_mode": "bottom", "quiet_mode": False, "low_power": False,
    "show_system_info": True, "show_battery": True,
    "cpu_alert": 90, "ram_alert": 85, "alert_duration": 10, "alert_cooldown": 120,
    "focus_minutes": 25,
    "character": "robot", "parachute": True, "playful": True,
    "note_reminders": True, "note_repeat_minutes": 30,
    "mouse_mode": "watch", "hide_seconds": 12, "folder_play": True,
    "ai_model": "openai/gpt-oss-20b", "ai_share_metrics": True, "ai_share_notes": True,
    "mini_hud": True, "desktop_enabled": True, "desktop_random": True,
    "portal_toys": False,
    "ai_autonomy": True, "ai_interval_seconds": 120, "ai_share_desktop_names": False,
    "business_mode": True, "business_name": "", "ai_greetings": True,
}


def data_directory() -> Path:
    """Prefer portable data beside the app; use per-user storage if read-only."""
    preferred = ROOT / "data"
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        probe = preferred / ".write-check"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return preferred
    except OSError:
        fallback = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "PixelSystemBuddy"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def clamp_position(position, width: int, height: int, screens):
    """Keep a whole window visible, including on monitors with negative origins."""
    from PySide6.QtCore import QPoint
    point = QPoint(int(position[0]), int(position[1]))
    screen = next((s for s in screens if s.availableGeometry().contains(point)), screens[0])
    rect = screen.availableGeometry()
    x = min(max(point.x(), rect.left()), max(rect.left(), rect.right() - width + 1))
    y = min(max(point.y(), rect.top()), max(rect.top(), rect.bottom() - height + 1))
    return QPoint(x, y)
