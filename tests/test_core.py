"""Regression checks for persistence, byte rates, and fallback sampling."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from settings import AppSettings
from system_stats import SystemStats, format_bytes
from alerts import LoadAlerts
from focus import FocusClock
from config import DEFAULTS


class SettingsTests(unittest.TestCase):
    def test_roundtrip_and_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            settings = AppSettings(path)
            settings.save({"opacity": 200, "speed": -20, "pet_position": [-1400, 250], "speech": False})
            reloaded = AppSettings(path)
            self.assertEqual(reloaded["opacity"], 100)
            self.assertEqual(reloaded["speed"], 5)
            self.assertEqual(reloaded["pet_position"], [-1400, 250])
            self.assertFalse(reloaded["speech"])

    def test_corrupt_settings_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text("{broken", encoding="utf-8")
            settings = AppSettings(path)
            self.assertTrue(settings.warning)
            self.assertTrue(path.with_suffix(".corrupt.json").exists())
            settings.save()
            self.assertTrue(AppSettings(path)["pet_enabled"])

    def test_wrong_types_do_not_enter_ui(self):
        safe = AppSettings.validate({"pet_size": "huge", "opacity": True, "show_cpu": "false", "overlay_position": [float("inf"), 0]})
        self.assertEqual(safe["pet_size"], 96)
        self.assertEqual(safe["opacity"], 88)
        self.assertTrue(safe["show_cpu"])
        self.assertIsNone(safe["overlay_position"])

    def test_upgrade_defaults_and_names(self):
        old = AppSettings.validate({"speed": 50})
        self.assertEqual(old["theme"], "midnight")
        self.assertEqual(old["roaming_mode"], "bottom")
        self.assertFalse(old["quiet_mode"])
        customized = AppSettings.validate({"pet_name": "\nPip\t", "theme": "unknown", "cpu_alert": 200})
        self.assertEqual(customized["pet_name"], "Pip")
        self.assertEqual(customized["theme"], "midnight")
        self.assertEqual(customized["cpu_alert"], 100)


class StatsTests(unittest.TestCase):
    def test_elapsed_time_rates_and_counter_reset(self):
        worker = SystemStats()
        snapshots = []
        worker.updated.connect(snapshots.append)
        network = [SimpleNamespace(bytes_sent=1000, bytes_recv=2000), SimpleNamespace(bytes_sent=3048, bytes_recv=6096), SimpleNamespace(bytes_sent=0, bytes_recv=0)]
        disk = [SimpleNamespace(read_bytes=100, write_bytes=100), SimpleNamespace(read_bytes=2100, write_bytes=4100), SimpleNamespace(read_bytes=1, write_bytes=1)]
        with patch("system_stats.time.monotonic", side_effect=[100, 102, 104]), patch("system_stats.psutil.net_io_counters", side_effect=network), patch("system_stats.psutil.disk_io_counters", side_effect=disk):
            worker.sample()
            worker.sample()
            worker.sample()
        self.assertIsNone(snapshots[0].upload)
        self.assertEqual(snapshots[1].upload, 1024)
        self.assertEqual(snapshots[1].download, 2048)
        self.assertEqual(snapshots[1].read_rate, 1000)
        self.assertEqual(snapshots[1].write_rate, 2000)
        self.assertEqual(snapshots[2].upload, 0)

    def test_missing_io_and_disk_are_safe(self):
        worker = SystemStats()
        snapshots = []
        worker.updated.connect(snapshots.append)
        with patch("system_stats.psutil.disk_usage", side_effect=PermissionError), patch("system_stats.psutil.net_io_counters", return_value=None), patch("system_stats.psutil.disk_io_counters", return_value=None):
            worker.sample()
        self.assertIsNone(snapshots[0].disk_percent)
        self.assertIsNone(snapshots[0].download)
        self.assertIsNone(snapshots[0].gpu_percent)
        self.assertIsNotNone(snapshots[0].memory_percent)

    def test_optional_battery_and_process_count(self):
        worker = SystemStats()
        snapshots = []
        worker.updated.connect(snapshots.append)
        with patch("system_stats.psutil.sensors_battery", return_value=SimpleNamespace(percent=63, power_plugged=False)), patch("system_stats.psutil.pids", return_value=[1, 2, 3]):
            worker.sample()
        self.assertEqual(snapshots[0].battery_percent, 63)
        self.assertFalse(snapshots[0].battery_plugged)
        self.assertEqual(snapshots[0].process_count, 3)

    def test_rate_units(self):
        self.assertEqual(format_bytes(2048, True), "2.0 KB/s")
        self.assertEqual(format_bytes(-1, True), "0 B/s")
        self.assertEqual(format_bytes(1024**3), "1.0 GB")


class AlertTests(unittest.TestCase):
    def test_sustained_cpu_and_repeat_cooldown(self):
        alerts = LoadAlerts()
        s = dict(DEFAULTS, cpu_alert=75, alert_duration=5, alert_cooldown=30)
        self.assertIsNone(alerts.evaluate(80, 20, s, 0))
        self.assertIsNone(alerts.evaluate(80, 20, s, 4))
        self.assertEqual(alerts.evaluate(80, 20, s, 5), "CPU working hard!")
        self.assertIsNone(alerts.evaluate(80, 20, s, 34))
        self.assertEqual(alerts.evaluate(80, 20, s, 35), "CPU working hard!")

    def test_transient_spike_and_missing_cpu_reset_duration(self):
        alerts = LoadAlerts()
        s = dict(DEFAULTS)
        alerts.evaluate(99, 20, s, 0)
        alerts.evaluate(None, 20, s, 6)
        self.assertIsNone(alerts.evaluate(99, 20, s, 11))
        self.assertEqual(alerts.evaluate(99, 20, s, 21), "CPU working hard!")

    def test_quiet_mode_does_not_consume_warning(self):
        alerts = LoadAlerts()
        s = dict(DEFAULTS)
        self.assertIsNone(alerts.evaluate(20, 95, s, 10, notify=False))
        self.assertEqual(alerts.evaluate(20, 95, s, 11), "Memory getting full!")
        self.assertIsNone(alerts.evaluate(20, 95, s, 30))


class FocusTests(unittest.TestCase):
    def test_pause_resume_preserves_remaining_time(self):
        now = [100.0]
        clock = FocusClock(lambda: now[0])
        clock.start(1)
        now[0] = 110.0
        clock.pause()
        self.assertEqual(clock.remaining(), 50)
        now[0] = 500.0
        self.assertEqual(clock.remaining(), 50)
        clock.resume()
        now[0] = 549.0
        self.assertFalse(clock.tick())
        self.assertEqual(clock.label(), "Focus time · 00:01")
        now[0] = 550.0
        self.assertTrue(clock.tick())
        self.assertFalse(clock.tick(), "Completion repeated")

    def test_delayed_tick_and_reset(self):
        now = [0.0]
        clock = FocusClock(lambda: now[0])
        clock.start(2)
        now[0] = 300.0
        self.assertTrue(clock.tick())
        clock.reset()
        self.assertEqual(clock.state, "idle")
        self.assertEqual(clock.label(), "")


if __name__ == "__main__":
    unittest.main()
