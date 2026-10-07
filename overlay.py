"""Compact hand-painted HUD with timestamp-based, sixty-second graphs."""
from __future__ import annotations

from collections import deque
from PySide6.QtCore import Qt, Signal, QRectF, QPointF
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QFont
from PySide6.QtWidgets import QApplication, QWidget
from config import clamp_position
from system_stats import Snapshot, format_bytes
from themes import palette

COLORS = {"cpu": "#60c9ff", "ram": "#b29aff", "disk": "#83dbaf", "network": "#ffbf7b", "gpu": "#ef99d4", "battery": "#e2d989"}


class StatsOverlay(QWidget):
    position_changed = Signal(object)
    menu_requested = Signal(object)
    settings_requested = Signal()

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.snapshot = Snapshot()
        self.history = deque(maxlen=130)
        self.drag_offset = None
        self.focus_label = ""
        self.effective_compact = settings["compact"]
        self.setWindowTitle("PixelSystem Buddy · Monitor")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setToolTip('Drag to move · Right-click for settings · Turn off Tiny HUD for full details')
        self.configure()
        screen = QApplication.primaryScreen().availableGeometry()
        position = settings["overlay_position"] or [screen.right() - self.width() - 24, screen.top() + 28]
        self.move(clamp_position(position, self.width(), self.height(), QApplication.screens()))

    def configure(self):
        flags = Qt.FramelessWindowHint | Qt.Tool
        if self.settings["always_on_top"]:
            flags |= Qt.WindowStaysOnTopHint
        if self.settings["click_through"]:
            flags |= Qt.WindowTransparentForInput
        visible = self.isVisible()
        self.setWindowFlags(flags)
        self.setWindowOpacity(self.settings["opacity"] / 100)
        self.resize_for_rows()
        if visible:
            self.show()
        self.update()

    def rows(self):
        s = self.snapshot
        percent = lambda n: "—" if n is None else f"{n:.0f}%"
        rate = lambda n: "—" if n is None else format_bytes(n, True)
        result = []
        if self.settings["show_cpu"]:
            detail = f"{s.frequency_mhz / 1000:.2f} GHz" if s.frequency_mhz else "Live utilization"
            result.append(("cpu", "CPU", percent(s.cpu), detail, s.cpu))
        if self.settings["show_ram"]:
            detail = f"{format_bytes(s.memory_used)} / {format_bytes(s.memory_total)}" if s.memory_total else "Reading memory…"
            result.append(("ram", "MEMORY", percent(s.memory_percent), detail, s.memory_percent))
        if self.settings["show_disk"]:
            import sys
            label = "DISK C:" if sys.platform == "win32" else "DISK /"
            result.append(("disk", label, percent(s.disk_percent), f"R {rate(s.read_rate)}  ·  W {rate(s.write_rate)}", s.disk_percent))
        if self.settings["show_network"]:
            result.append(("network", "NETWORK", rate(s.download), f"↓ Download    ↑ {rate(s.upload)}", s.download))
        if self.settings["show_gpu"] and s.gpu_percent is not None:
            detail = s.gpu_name
            if s.gpu_memory:
                detail += " · " + format_bytes(s.gpu_memory) + " dedicated"
            result.append(("gpu", "GPU", percent(s.gpu_percent), detail, s.gpu_percent))
        if self.settings["show_battery"] and s.battery_percent is not None:
            result.append(("battery", "BATTERY", percent(s.battery_percent), "Plugged in" if s.battery_plugged else "On battery", s.battery_percent))
        return result

    def resize_for_rows(self):
        count = max(1, len(self.rows()))
        if self.settings["mini_hud"]:
            self.effective_compact = True
            self.resize(224, 68 + 28 * count)
            return
        screen = QApplication.screenAt(self.pos()) or QApplication.primaryScreen()
        self.effective_compact = self.settings["compact"] or (115 + 80 * count > screen.availableGeometry().height() - 8)
        row_height = 53 if self.effective_compact else 80
        self.resize(334 if self.effective_compact else 378, 115 + row_height * count)

    def set_focus_label(self, text):
        self.focus_label = text
        if self.isVisible():
            self.update()

    def accept_snapshot(self, snapshot):
        self.snapshot = snapshot
        self.history.append(snapshot)
        while self.history and snapshot.timestamp - self.history[0].timestamp > 60:
            self.history.popleft()
        self.resize_for_rows()
        self.move(clamp_position([self.x(), self.y()], self.width(), self.height(), QApplication.screens()))
        if self.isVisible():
            self.update()

    def graph(self, painter, key, rect, color):
        field = {"cpu": "cpu", "ram": "memory_percent", "disk": "disk_percent", "network": "download", "gpu": "gpu_percent", "battery": "battery_percent"}[key]
        values = [(s.timestamp, getattr(s, field)) for s in self.history]
        valid = [v for _, v in values if v is not None]
        maximum = max(1024, max(valid, default=0) * 1.15) if key == "network" else 100
        painter.setPen(QPen(QColor(255, 255, 255, 12), 1))
        for i in range(1, 4):
            y = rect.top() + i * rect.height() / 4
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        line = QPainterPath()
        start = self.snapshot.timestamp - 60
        drawing = False
        for timestamp, value in values:
            if value is None:
                drawing = False
                continue
            point = QPointF(rect.left() + (timestamp - start) / 60 * rect.width(), rect.bottom() - min(1, max(0, value / maximum)) * rect.height())
            if drawing:
                line.lineTo(point)
            else:
                line.moveTo(point)
            drawing = True
        painter.setPen(QPen(QColor(color), 1.7))
        painter.drawPath(line)

    def paintEvent(self, event):
        p = QPainter(self)
        colors = palette(self.settings)
        if self.settings["mini_hud"]:
            self.paint_mini(p, colors)
            p.end()
            return
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(colors["bg"]))
        p.setPen(QPen(QColor(colors["border"]), 1))
        p.drawRoundedRect(QRectF(1, 1, self.width() - 2, self.height() - 2), 16, 16)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(colors["accent"]))
        p.drawEllipse(QRectF(19, 23, 7, 7))
        p.setPen(QColor(colors["text"]))
        p.setFont(QFont("Segoe UI", 10, QFont.Bold))
        p.drawText(34, 32, "PIXELSYSTEM")
        p.setFont(QFont("Segoe UI", 8))
        p.setPen(QColor(colors["muted"]))
        p.drawText(QRectF(self.width() - 110, 16, 89, 23), Qt.AlignRight | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(self.settings["pet_name"], Qt.ElideRight, 89))
        processor = self.snapshot.processor or "Connecting to your system…"
        p.drawText(19, 52, p.fontMetrics().elidedText(processor, Qt.ElideRight, self.width() - 38))
        p.setPen(QColor(colors["border"]))
        p.drawLine(19, 63, self.width() - 19, 63)
        compact = self.effective_compact
        row_height = 53 if compact else 80
        for i, (key, label, value, detail, _) in enumerate(self.rows()):
            y = 73 + i * row_height
            color = QColor(COLORS[key])
            p.fillRect(QRectF(19, y + 4, 3, row_height - 16), color)
            p.setFont(QFont("Segoe UI", 8, QFont.Bold))
            p.setPen(color)
            p.drawText(32, y + 13, label)
            p.setFont(QFont("Segoe UI", 14 if not compact else 12, QFont.Bold))
            warning = key == "cpu" and self.snapshot.cpu is not None and self.snapshot.cpu >= self.settings["cpu_alert"]
            warning = warning or key == "ram" and self.snapshot.memory_percent is not None and self.snapshot.memory_percent >= self.settings["ram_alert"]
            p.setPen(QColor("#ffba86" if warning else colors["text"]))
            max_width = self.width() - 169 if self.settings["graphs"] else self.width() - 54
            p.drawText(32, y + 35, p.fontMetrics().elidedText(value, Qt.ElideRight, max_width))
            if not compact:
                p.setFont(QFont("Segoe UI", 8))
                p.setPen(QColor(colors["muted"]))
                p.drawText(32, y + 55, p.fontMetrics().elidedText(detail, Qt.ElideRight, self.width() - 51))
            if self.settings["graphs"]:
                self.graph(p, key, QRectF(self.width() - 128, y + 3, 105, 34 if compact else 36), COLORS[key])
        if not self.rows():
            p.setFont(QFont("Segoe UI", 9))
            p.drawText(25, 103, "Enable a metric in Settings.")
        p.setPen(QColor(colors["muted"]))
        p.setFont(QFont("Segoe UI", 7))
        info = self.focus_label
        if not info and self.settings["show_system_info"]:
            s = self.snapshot
            parts = []
            if s.uptime_seconds is not None:
                hours, minutes = divmod(int(s.uptime_seconds // 60), 60)
                parts.append(f"Uptime {hours // 24}d {hours % 24:02d}:{minutes:02d}")
            if s.process_count is not None:
                parts.append(f"{s.process_count} processes")
            info = "  ·  ".join(parts)
        if self.settings["low_power"]:
            info = (info + "  ·  " if info else "") + "Low power"
        if self.settings["quiet_mode"]:
            info = (info + "  ·  " if info else "") + "Quiet"
        p.setPen(QColor(colors["accent"] if self.focus_label else colors["muted"]))
        p.drawText(19, self.height() - 34, p.fontMetrics().elidedText(info, Qt.ElideRight, self.width() - 38))
        p.setPen(QColor(colors["muted"]))
        p.drawText(19, self.height() - 13, "60s history  ·  Drag to move  ·  Right-click for settings")
        p.end()

    def paint_mini(self, p, colors):
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(colors["bg"]))
        p.setPen(QPen(QColor(colors["border"]), 1))
        p.drawRoundedRect(QRectF(1, 1, self.width() - 2, self.height() - 2), 10, 10)
        p.setPen(QColor(colors["accent"]))
        p.setFont(QFont("Segoe UI", 8, QFont.Bold))
        p.drawText(12, 23, "SYSTEM")
        p.setFont(QFont("Segoe UI", 7))
        p.setPen(QColor(colors["muted"]))
        p.drawText(QRectF(86, 9, 126, 20), Qt.AlignRight | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(self.settings["pet_name"], Qt.ElideRight, 126))
        for index, (key, label, value, detail, _) in enumerate(self.rows()):
            y = 33 + index * 28
            p.setFont(QFont("Segoe UI", 8))
            p.setPen(QColor(COLORS[key]))
            p.drawText(12, y + 17, "RAM" if key == "ram" else label.replace("DISK /", "DISK").replace("DISK C:", "DISK").replace("NETWORK", "NET"))
            warning = (key == 'cpu' and self.snapshot.cpu is not None and self.snapshot.cpu >= self.settings['cpu_alert']) or (key == 'ram' and self.snapshot.memory_percent is not None and self.snapshot.memory_percent >= self.settings['ram_alert'])
            p.setPen(QColor('#ffba86' if warning else colors["text"]))
            p.setFont(QFont("Segoe UI", 9, QFont.Bold))
            p.drawText(QRectF(52, y, 82, 24), Qt.AlignRight | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(value, Qt.ElideRight, 82))
            if self.settings["graphs"]:
                self.graph(p, key, QRectF(145, y + 3, 66, 19), COLORS[key])
        p.setFont(QFont("Segoe UI", 7))
        p.setPen(QColor(colors["muted"]))
        footer = self.focus_label or ("Quiet" if self.settings["quiet_mode"] else "Drag · Right-click")
        if not self.focus_label and not self.settings['quiet_mode'] and self.settings['show_system_info']:
            parts = []
            if self.snapshot.uptime_seconds is not None:
                hours, minutes = divmod(int(self.snapshot.uptime_seconds // 60), 60)
                parts.append(f'Up {hours}h {minutes:02d}m')
            if self.snapshot.process_count is not None:
                parts.append(f'{self.snapshot.process_count} processes')
            footer = ' · '.join(parts) or footer
        p.drawText(12, self.height() - 12, p.fontMetrics().elidedText(footer, Qt.ElideRight, 200))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_offset = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        if self.drag_offset is not None and event.buttons() & Qt.LeftButton:
            point = event.globalPosition().toPoint() - self.drag_offset
            self.move(clamp_position([point.x(), point.y()], self.width(), self.height(), QApplication.screens()))

    def mouseReleaseEvent(self, event):
        if self.drag_offset is not None:
            self.position_changed.emit([self.x(), self.y()])
        self.drag_offset = None

    def contextMenuEvent(self, event):
        self.menu_requested.emit(event.globalPos())

    def mouseDoubleClickEvent(self, event):
        self.settings_requested.emit()

    def closeEvent(self, event):
        event.accept()
        self.hide()
