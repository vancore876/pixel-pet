"""Four-second GUI integration check. Uses real CPU/RAM readings."""
import os
import sys
import tempfile
import json
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer, QRect
from PySide6.QtWidgets import QApplication
from main import BuddyApp
from settings import AppSettings
from config import clamp_position

app = QApplication([])
app.setQuitOnLastWindowClosed(False)
temp = tempfile.TemporaryDirectory()
settings = AppSettings(Path(temp.name) / "settings.json")
buddy = BuddyApp(app, settings, show_tray=False)
start_x = buddy.pet.x()
buddy.pet.next_state += 100
buddy.show_processes()
errors = []


def check():
    try:
        assert buddy.overlay.snapshot.cpu is not None, "No CPU reading"
        assert buddy.overlay.snapshot.memory_percent is not None, "No RAM reading"
        assert len(buddy.overlay.history) >= 2, "Stats not updating"
        assert buddy.overlay.snapshot.process_count is not None, "Missing process count"
        assert buddy.process_window.rows, "On-demand process list did not populate"
        assert any(row["cpu"] is not None for row in buddy.process_window.rows), "Process CPU not sampled twice"
        process_rows = list(buddy.process_window.rows)
        buddy.process_window.hide()
        assert buddy.pet.x() != start_x, "Pet did not move"
        buddy.pet.set_paused(True)
        paused_x = buddy.pet.x()
        buddy.pet.animate()
        assert buddy.pet.x() == paused_x, "Pause failed"
        buddy.pet.set_paused(False)
        buddy.apply_settings({"opacity": 75, "compact": True, "interval_ms": 500})
        assert AppSettings(settings.path)["opacity"] == 75, "Settings did not save"
        buddy.apply_settings({"click_through": True})
        assert buddy.settings["click_through"], "Could not lock overlay"
        buddy.click_action.trigger()
        assert not buddy.settings["click_through"], "Menu failed to unlock overlay"
        buddy.apply_settings({"roaming_mode": "free", "pet_name": "Nova", "theme": "forest", "pet_palette": "amber"})
        rect = buddy.pet.monitor_rect()
        buddy.pet.move(rect.left() + 150, rect.top() + 150)
        buddy.pet.real_x, buddy.pet.real_y = float(buddy.pet.x()), float(buddy.pet.y())
        buddy.pet.state = "WALK_RIGHT"
        buddy.pet.vertical_direction = 0.5
        buddy.pet.last_tick = time.monotonic() - 0.1
        buddy.pet.animate()
        assert buddy.pet.y() < rect.bottom() - buddy.pet.height() - 1, "Free Buddy snapped to bottom"
        buddy.pet.say("Hello!")
        assert buddy.pet.bubble.text.startswith("Nova:"), "Buddy name missing"
        buddy.apply_settings({"quiet_mode": True, "low_power": True})
        buddy.pet.say("Should stay quiet")
        assert not buddy.pet.bubble.isVisible(), "Quiet mode leaked a bubble"
        assert buddy.effective_interval() >= 2000, "Low power did not reduce sampling"
        draft = Path(temp.name) / "backup.json"
        draft.write_text(json.dumps({"theme": "plum", "pet_name": "Draft", "start_with_windows": True, "overlay_position": [99999, 99999]}))
        buddy.dialog.load_draft(draft)
        assert buddy.settings["theme"] == "forest", "Import applied without Apply"
        assert not buddy.dialog.controls["start_with_windows"].isChecked(), "Import changed startup"
        buddy.dialog.apply()
        assert buddy.settings["theme"] == "plum", "Imported theme not applied"
        buddy.focus_start_action.trigger()
        assert "Focus" in buddy.overlay.focus_label, "Focus display did not update"
        buddy.focus_pause_action.trigger()
        assert buddy.focus.clock.state == "paused", "Focus pause failed"
        buddy.focus_pause_action.trigger()
        assert buddy.focus.clock.state == "running", "Focus resume failed"
        buddy.pet.real_x = 100000
        buddy.pet.real_y = 100000
        buddy.pet.animate()
        rect = buddy.pet.monitor_rect()
        assert rect.contains(buddy.pet.geometry()), "Pet escaped screen"
        class Screen:
            def availableGeometry(self):
                return QRect(-1920, -150, 1920, 1080)
        point = clamp_position([-1800, -100], 300, 200, [Screen()])
        assert point.x() == -1800 and point.y() == -100, "Negative monitor origins broken"
        buddy.dialog.refresh(1)
        buddy.dialog.show()
        app.processEvents()
        preview = Path(os.environ.get("BUDDY_QA_DIR", temp.name))
        preview.mkdir(parents=True, exist_ok=True)
        buddy.apply_settings({"compact": False, "opacity": 100, "pet_name": "Pip"})
        buddy.dialog.refresh(1)
        app.processEvents()
        buddy.overlay.grab().save(str(preview / "overlay-live.png"))
        buddy.pet.grab().save(str(preview / "pet.png"))
        buddy.dialog.grab().save(str(preview / "settings.png"))
        buddy.dialog.refresh(2)
        app.processEvents()
        buddy.dialog.grab().save(str(preview / "buddy-settings.png"))
        buddy.process_window.show()
        buddy.process_window.accept_rows(process_rows)
        app.processEvents()
        buddy.process_window.grab().save(str(preview / "processes.png"))
        buddy.process_window.hide()
        buddy.toggle_overlay()
        buddy.toggle_pet()
        assert buddy.overlay.isVisible(), "Missing-tray recovery stranded the app"
        # Trigger the actual Exit menu action, rather than calling shutdown directly.
        buddy.menu.actions()[-1].trigger()
    except Exception as exc:
        errors.append(str(exc))
        app.quit()


QTimer.singleShot(3500, check)
QTimer.singleShot(10000, app.quit)
app.exec()
assert not buddy.thread.isRunning(), "Worker remained alive after Exit"
assert not buddy.pet.timer.isActive(), "Pet timer remained alive after Exit"
assert not buddy.focus.timer.isActive(), "Focus timer remained alive after Exit"
temp.cleanup()
if errors:
    raise AssertionError("; ".join(errors))
print("PASS: live stats, on-demand processes, movement, free roaming, quiet mode, low power, theme/name/skin, settings import draft, focus timer, click-through recovery, bounds, rendering, Exit cleanup")
