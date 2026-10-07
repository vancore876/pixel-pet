"""Regressions for bounded repainting, hidden timers and worker lifecycle."""
from unittest.mock import patch
import time
import unittest

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from config import DEFAULTS
from motion import HopMotion
from overlay import StatsOverlay
from pet import PixelPet
from process_window import ProcessWindow
from system_stats import Snapshot, SystemStats


app = QApplication.instance() or QApplication([])


class RuntimePerformanceTests(unittest.TestCase):
    def setUp(self):
        self.windows = []
        self.workers = []

    def tearDown(self):
        for worker in self.workers:
            worker.stop()
        for window in self.windows:
            window.hide()
            window.deleteLater()
        app.processEvents()

    def window(self, cls, **changes):
        window = cls(dict(DEFAULTS, **changes))
        self.windows.append(window)
        return window

    def test_torch_repaints_reuse_metrics_and_invalidate_on_real_changes(self):
        overlay = self.window(StatsOverlay, mini_hud=False)
        overlay.accept_snapshot(Snapshot(timestamp=100, cpu=20, memory_percent=30))
        with patch.object(overlay, 'paint_keep_content', wraps=overlay.paint_keep_content) as paint:
            first = overlay.keep_layers()[1].cacheKey()
            for _ in range(20):
                overlay.animate_decoration()
                self.assertEqual(overlay.keep_layers()[1].cacheKey(), first)
            self.assertEqual(paint.call_count, 1)
            overlay.accept_snapshot(Snapshot(timestamp=101, cpu=75, memory_percent=40))
            self.assertNotEqual(overlay.keep_layers()[1].cacheKey(), first)
            self.assertEqual(paint.call_count, 2)
            overlay.set_focus_label('Focus time · 02:00')
            overlay.keep_layers()
            self.assertEqual(paint.call_count, 3)
            overlay.resize(overlay.width() + 20, overlay.height())
            overlay.keep_layers()
            self.assertEqual(paint.call_count, 4)
            overlay.settings['pet_name'] = 'Updated name'
            overlay.configure()
            overlay.keep_layers()
            self.assertEqual(paint.call_count, 5)

    def test_hidden_pet_uses_no_animation_timer_and_show_resumes_motion(self):
        pet = self.window(PixelPet, speech=False)
        self.assertFalse(pet.timer.isActive())
        pet.show()
        self.assertTrue(pet.timer.isActive())
        pet.hide()
        self.assertFalse(pet.timer.isActive())
        pet.hop_motion = HopMotion(pet.x(), pet.y(), pet.x() + 20, pet.y())
        pet.last_tick = 0
        before_show = time.monotonic()
        pet.show()
        self.assertTrue(pet.timer.isActive())
        self.assertEqual(pet.timer.interval(), 80)
        self.assertGreaterEqual(pet.last_tick, before_show)
        pet.hide()
        self.assertFalse(pet.timer.isActive())

    def test_cached_monitor_preserves_frame_torch_and_parchment_layer_order(self):
        for mini in (True, False):
            with self.subTest(mini=mini):
                overlay = self.window(StatsOverlay, mini_hud=mini)
                overlay.accept_snapshot(Snapshot(timestamp=100, cpu=99, memory_percent=67))
                overlay.animation_phase = 1.2
                direct, cached = QPixmap(overlay.size()), QPixmap(overlay.size())
                direct.fill(Qt.transparent)
                cached.fill(Qt.transparent)
                painter = QPainter(direct)
                overlay.paint_keep(painter)
                painter.end()
                painter = QPainter(cached)
                frame, content = overlay.keep_layers()
                painter.drawPixmap(0, 0, frame)
                painter.setRenderHint(QPainter.Antialiasing)
                overlay.paint_torch(painter)
                painter.drawPixmap(0, 0, content)
                painter.end()
                direct_image, cached_image = direct.toImage(), cached.toImage()
                direct_bytes = bytes(direct_image.constBits())
                cached_bytes = bytes(cached_image.constBits())
                # Qt can round a channel by one when composing cached alpha.
                self.assertLessEqual(max(abs(a - b) for a, b in zip(direct_bytes, cached_bytes)), 1)

    def test_process_refresh_reuses_cells_and_keeps_selected_process(self):
        window = self.window(ProcessWindow)
        window.rows = [
            {'pid': index, 'name': f'App {index}', 'cpu': index / 10, 'memory': index * 1000}
            for index in range(100)
        ]
        window.render()
        self.assertEqual(window.table.rowCount(), 20)
        first_cell = window.table.item(0, 0)
        window.table.selectRow(0)
        window.rows[-1]['cpu'] = 9.6
        window.rows[-2]['cpu'] = 10
        window.render()
        self.assertIs(window.table.item(0, 0), first_cell)
        selected_row = window.table.selectionModel().selectedRows()[0].row()
        self.assertEqual(window.table.item(selected_row, 3).text(), '99')
        self.assertTrue(window.table.updatesEnabled())
        window.rows = window.rows[:50]
        window.render()
        self.assertFalse(window.table.selectionModel().selectedRows())

    def test_sampling_restart_reuses_timers_and_resets_rate_baselines(self):
        worker = SystemStats()
        self.workers.append(worker)
        with patch.object(worker, 'sample') as sample, patch('system_stats.WindowsGPU'):
            worker.start()
            worker.start()
            self.assertEqual(sample.call_count, 1)
            timer, process_timer = worker.timer, worker.process_timer
            worker.last_time = 100
            worker.last_network = object()
            worker.last_disk = object()
            worker.process_cache[(1, 0)] = object()
            worker.stop()
            self.assertFalse(worker.process_cache)
            worker.start()
            self.assertIs(worker.timer, timer)
            self.assertIs(worker.process_timer, process_timer)
            self.assertEqual(len(worker.findChildren(QTimer)), 2)
            self.assertEqual(worker.last_time, 0)
            self.assertIsNone(worker.last_network)
            self.assertIsNone(worker.last_disk)

    def test_duplicate_process_enable_keeps_cpu_sampling_baseline(self):
        worker = SystemStats()
        self.workers.append(worker)
        with patch.object(worker, 'sample'), patch('system_stats.WindowsGPU'):
            worker.start()
        with patch.object(worker, 'sample_processes') as sample:
            worker.set_process_monitor(True)
            worker.process_cache[(1, 0)] = 'baseline'
            worker.set_process_monitor(True)
            self.assertEqual(sample.call_count, 1)
            self.assertEqual(worker.process_cache[(1, 0)], 'baseline')
            worker.set_process_monitor(False)
            self.assertFalse(worker.process_timer.isActive())
            self.assertFalse(worker.process_cache)

    def test_hops_finish_with_zero_negative_or_very_short_duration(self):
        for duration in (0, -1, 0.001):
            with self.subTest(duration=duration):
                hop = HopMotion(0, 0, 10, 20, duration=duration)
                self.assertEqual(hop.step(0.01), (10, 20, True))


if __name__ == '__main__':
    unittest.main()
