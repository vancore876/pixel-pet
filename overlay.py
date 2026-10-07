"""Compact system keep or classic HUD with real, sixty-second metric graphs."""
from __future__ import annotations

from collections import deque
import math
from PySide6.QtCore import Qt, Signal, QRectF, QPointF, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QFont, QLinearGradient, QPixmap
from PySide6.QtWidgets import QApplication, QWidget
from config import clamp_position
from system_stats import Snapshot, format_bytes
from themes import palette

COLORS = {"cpu": "#60c9ff", "ram": "#b29aff", "disk": "#83dbaf", "network": "#ffbf7b", "gpu": "#ef99d4", "battery": "#e2d989"}
KEEP_COLORS = {"cpu": "#345f79", "ram": "#694a82", "disk": "#386748", "network": "#926031", "gpu": "#8b4167", "battery": "#706823"}


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
        self.animation_phase = 0.0
        self._keep_layers = None
        self._keep_layers_key = None
        self.animation_timer = QTimer(self)
        self.animation_timer.setInterval(120)
        self.animation_timer.timeout.connect(self.animate_decoration)
        QApplication.instance().aboutToQuit.connect(self.animation_timer.stop)
        self.setWindowTitle("PixelSystem Buddy · Monitor")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setToolTip('Drag to move · Right-click for settings · Turn off Tiny HUD for full details')
        self.configure()
        screen = QApplication.primaryScreen().availableGeometry()
        position = settings["overlay_position"] or [screen.right() - self.width() - 24, screen.top() + 28]
        self.move(clamp_position(position, self.width(), self.height(), QApplication.screens()))

    def configure(self):
        self.invalidate_keep_layers()
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
        self.sync_animation()
        self.update()

    def sync_animation(self):
        """Decorative repainting only runs while the keep is visible and active."""
        animate = (self.isVisible() and self.settings["monitor_style"] == "medieval"
                   and not self.settings["low_power"] and not self.settings["quiet_mode"])
        if animate:
            if not self.animation_timer.isActive():
                self.animation_timer.start()
        else:
            self.animation_timer.stop()
            self.animation_phase = 0.0

    def animate_decoration(self):
        self.animation_phase = (self.animation_phase + 0.45) % (math.pi * 2)
        # Only the little torch needs a redraw; metrics still follow sampling.
        self.update(7, 4, 24, 31)

    def stop_animation(self):
        self.animation_timer.stop()

    def invalidate_keep_layers(self):
        self._keep_layers = None
        self._keep_layers_key = None

    def keep_layers(self):
        """Keep torch animation from rebuilding every metric and graph."""
        scale = self.devicePixelRatioF()
        key = (self.width(), self.height(), scale)
        if self._keep_layers is None or self._keep_layers_key != key:
            layers = []
            # The parchment covers the bottom of the torch in the tiny HUD.
            # Keep that original drawing order when caching the static work.
            for draw in (self.paint_keep_frame, self.paint_keep_content):
                image = QPixmap(self.size() * scale)
                image.setDevicePixelRatio(scale)
                image.fill(Qt.transparent)
                painter = QPainter(image)
                try:
                    draw(painter)
                finally:
                    painter.end()
                layers.append(image)
            self._keep_layers = tuple(layers)
            self._keep_layers_key = key
        return self._keep_layers

    def showEvent(self, event):
        super().showEvent(event)
        self.sync_animation()

    def hideEvent(self, event):
        self.animation_timer.stop()
        super().hideEvent(event)

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
        self.invalidate_keep_layers()
        if self.isVisible():
            self.update()

    def accept_snapshot(self, snapshot):
        self.snapshot = snapshot
        self.invalidate_keep_layers()
        self.history.append(snapshot)
        while self.history and snapshot.timestamp - self.history[0].timestamp > 60:
            self.history.popleft()
        self.resize_for_rows()
        self.move(clamp_position([self.x(), self.y()], self.width(), self.height(), QApplication.screens()))
        if self.isVisible():
            self.update()

    def graph(self, painter, key, rect, color, grid_color=None):
        field = {"cpu": "cpu", "ram": "memory_percent", "disk": "disk_percent", "network": "download", "gpu": "gpu_percent", "battery": "battery_percent"}[key]
        values = [(s.timestamp, getattr(s, field)) for s in self.history]
        valid = [v for _, v in values if v is not None]
        maximum = max(1024, max(valid, default=0) * 1.15) if key == "network" else 100
        painter.setPen(QPen(grid_color or QColor(255, 255, 255, 12), 1))
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
        if self.settings["monitor_style"] == "medieval":
            frame, content = self.keep_layers()
            p.drawPixmap(0, 0, frame)
            p.setRenderHint(QPainter.Antialiasing)
            self.paint_torch(p)
            p.drawPixmap(0, 0, content)
            p.end()
            return
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

    def paint_torch(self, p):
        """A small CPU-fed flame; it never substitutes for the numeric readings."""
        cpu = self.snapshot.cpu
        activity = min(1.0, max(0.0, cpu / 100)) if cpu is not None else 0.0
        sway = math.sin(self.animation_phase) * (0.5 + activity)
        height = 11 + activity * 5 + math.sin(self.animation_phase * 2) * activity
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(242, 177, 81, 22 if cpu is not None else 0))
        p.drawEllipse(QRectF(7, 6, 23, 27))
        p.setBrush(QColor("#a07a40"))
        p.drawRoundedRect(QRectF(16, 25, 5, 9), 1, 1)
        p.setPen(QPen(QColor("#d1b77a"), 1))
        p.drawLine(QPointF(13, 26), QPointF(24, 26))
        flame = QPainterPath(QPointF(18.5, 26))
        flame.cubicTo(9, 23, 15, 16, 18.5 + sway, 25 - height)
        flame.cubicTo(20 + sway, 18, 29, 22, 18.5, 26)
        gradient = QLinearGradient(0, 25 - height, 0, 26)
        gradient.setColorAt(0, QColor("#ffe8ab" if cpu is not None else "#b5a68e"))
        gradient.setColorAt(1, QColor("#c67737" if cpu is not None else "#746653"))
        p.setPen(Qt.NoPen)
        p.setBrush(gradient)
        p.drawPath(flame)
        if cpu is not None:
            p.setBrush(QColor("#ffe7a5"))
            p.drawEllipse(QRectF(16, 20, 5, 6))

    def keep_footer(self, mini=False):
        if self.focus_label:
            return self.focus_label
        parts = []
        if self.settings["quiet_mode"]:
            parts.append("Quiet")
        if self.settings["low_power"]:
            parts.append("Low power")
        if self.settings["show_system_info"]:
            s = self.snapshot
            if s.uptime_seconds is not None:
                hours, minutes = divmod(int(s.uptime_seconds // 60), 60)
                parts.append(f"Up {hours}h {minutes:02d}m" if mini else f"Uptime {hours // 24}d {hours % 24:02d}:{minutes:02d}")
            if s.process_count is not None:
                parts.append(f"{s.process_count} processes")
        return " · ".join(parts) or ("Drag · Right-click" if mini else "")

    def paint_keep(self, p, include_torch=True):
        """Legible parchment readings inside a carved wood and brass frame."""
        self.paint_keep_frame(p)
        if include_torch:
            self.paint_torch(p)
        self.paint_keep_content(p)

    def paint_keep_frame(self, p):
        p.setRenderHint(QPainter.Antialiasing)
        width, height = self.width(), self.height()
        mini = self.settings["mini_hud"]
        wood = QLinearGradient(0, 0, width, height)
        wood.setColorAt(0, QColor("#4b352a"))
        wood.setColorAt(0.5, QColor("#32261f"))
        wood.setColorAt(1, QColor("#241d19"))
        p.setBrush(wood)
        p.setPen(QPen(QColor("#b99a5b"), 1.2))
        p.drawRoundedRect(QRectF(1, 1, width - 2, height - 2), 10 if mini else 14, 10 if mini else 14)
        p.setPen(QPen(QColor("#6e5135"), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(4, 4, width - 8, height - 8), 8 if mini else 12, 8 if mini else 12)

    def paint_keep_content(self, p):
        p.setRenderHint(QPainter.Antialiasing)
        width, height = self.width(), self.height()
        mini = self.settings["mini_hud"]
        rows = self.rows()
        compact = self.effective_compact
        row_height = 28 if mini else 53 if compact else 80
        row_start = 33 if mini else 73
        p.setPen(QColor("#f4dfb2"))
        p.setFont(QFont("Georgia", 8 if mini else 10, QFont.Bold))
        p.drawText(QRectF(32, 7 if mini else 13, 104 if mini else 145, 22), Qt.AlignVCenter, "SYSTEM KEEP")
        p.setFont(QFont("Segoe UI", 7 if mini else 8))
        p.setPen(QColor("#d3bd95"))
        name_start = 138 if mini else width - 112
        name_width = width - name_start - 13
        p.drawText(QRectF(name_start, 8 if mini else 14, name_width, 22), Qt.AlignRight | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(self.settings["pet_name"], Qt.ElideRight, name_width))
        if not mini:
            processor = self.snapshot.processor or "Connecting to your system…"
            p.drawText(19, 52, p.fontMetrics().elidedText(processor, Qt.ElideRight, width - 38))
            p.setPen(QPen(QColor("#b99a5b"), 1))
            p.drawLine(19, 63, width - 19, 63)
        parchment = QLinearGradient(0, row_start, width, row_start + row_height * max(1, len(rows)))
        parchment.setColorAt(0, QColor("#f1e2bd"))
        parchment.setColorAt(1, QColor("#dfcda1"))
        p.setPen(QPen(QColor("#baa374"), 1))
        p.setBrush(parchment)
        p.drawRoundedRect(QRectF(8, row_start - 2 if mini else row_start - 5,
                                width - 16, row_height * max(1, len(rows)) + (4 if mini else 2)), 4, 4)
        for index, (key, label, value, detail, _) in enumerate(rows):
            y = row_start + index * row_height
            color = QColor(KEEP_COLORS[key])
            if index:
                p.setPen(QPen(QColor(116, 85, 49, 32), 1))
                p.drawLine(QPointF(15, y - 1), QPointF(width - 15, y - 1))
            warning = ((key == "cpu" and self.snapshot.cpu is not None and self.snapshot.cpu >= self.settings["cpu_alert"])
                       or (key == "ram" and self.snapshot.memory_percent is not None and self.snapshot.memory_percent >= self.settings["ram_alert"]))
            if mini:
                label = {"ram": "RAM", "disk": "DISK", "network": "NET"}.get(key, label)
                p.setFont(QFont("Segoe UI", 8, QFont.Bold))
                p.setPen(color)
                p.drawText(14, y + 17, label)
                p.setPen(QColor("#9d3928" if warning else "#302719"))
                p.setFont(QFont("Segoe UI", 9, QFont.Bold))
                value_width = 82 if self.settings["graphs"] else width - 67
                p.drawText(QRectF(52, y, value_width, 24), Qt.AlignRight | Qt.AlignVCenter,
                           p.fontMetrics().elidedText(value, Qt.ElideRight, value_width))
                graph_rect = QRectF(145, y + 3, 64, 19)
            else:
                p.fillRect(QRectF(19, y + 4, 3, row_height - 16), color)
                p.setFont(QFont("Segoe UI", 8, QFont.Bold))
                p.setPen(color)
                p.drawText(32, y + 13, label)
                p.setFont(QFont("Segoe UI", 12 if compact else 14, QFont.Bold))
                p.setPen(QColor("#9d3928" if warning else "#302719"))
                value_width = width - 169 if self.settings["graphs"] else width - 54
                p.drawText(32, y + 35, p.fontMetrics().elidedText(value, Qt.ElideRight, value_width))
                if not compact:
                    p.setFont(QFont("Segoe UI", 8))
                    p.setPen(QColor("#6c593b"))
                    p.drawText(32, y + 55, p.fontMetrics().elidedText(detail, Qt.ElideRight, width - 51))
                graph_rect = QRectF(width - 128, y + 3, 105, 34 if compact else 36)
            if self.settings["graphs"]:
                self.graph(p, key, graph_rect, KEEP_COLORS[key], QColor(97, 72, 43, 26))
        if not rows:
            p.setFont(QFont("Segoe UI", 7 if mini else 9))
            p.setPen(QColor("#4f402b"))
            p.drawText(QRectF(14, row_start, width - 28, 28), Qt.AlignVCenter, "Enable a metric in Settings.")
        p.setFont(QFont("Segoe UI", 7))
        p.setPen(QColor("#f1d9a5" if self.focus_label else "#d3bd95"))
        p.drawText(14 if mini else 19, height - 12 if mini else height - 34,
                   p.fontMetrics().elidedText(self.keep_footer(mini), Qt.ElideRight, width - (28 if mini else 38)))
        if not mini:
            p.setPen(QColor("#d3bd95"))
            p.drawText(19, height - 13, "60s history  ·  Drag to move  ·  Right-click for settings")

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
