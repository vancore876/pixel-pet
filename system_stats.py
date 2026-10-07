"""Nonblocking system sampling. Disk I/O is system-wide; capacity is C: on Windows."""
from __future__ import annotations

import platform
import sys
import time
from dataclasses import dataclass
import psutil
from PySide6.QtCore import QObject, QTimer, Signal, Slot


def format_bytes(value: float, rate: bool = False) -> str:
    value = max(0.0, value)
    unit = "B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            break
        value /= 1024
    return f"{value:.0f}" + f" {unit}" + ("/s" if rate else "") if unit == "B" else f"{value:.1f} {unit}" + ("/s" if rate else "")


@dataclass
class Snapshot:
    timestamp: float = 0
    cpu: float | None = None
    frequency_mhz: float | None = None
    memory_percent: float | None = None
    memory_used: int = 0
    memory_total: int = 0
    disk_percent: float | None = None
    read_rate: float | None = None
    write_rate: float | None = None
    upload: float | None = None
    download: float | None = None
    gpu_percent: float | None = None
    gpu_name: str = ""
    gpu_memory: int | None = None
    gpu_total: int | None = None
    processor: str = ""
    uptime_seconds: float | None = None
    process_count: int | None = None
    battery_percent: float | None = None
    battery_plugged: bool | None = None


class WindowsGPU:
    """Read Windows PDH GPU counters, including integrated graphics.

    Group engines across processes and report the busiest engine type. Driver
    support varies; counters or adapters unavailable means the section is hidden.
    """
    def __init__(self):
        self.query = None
        self.name = "GPU"
        if sys.platform != "win32":
            return
        import ctypes as c
        from ctypes import wintypes as w
        self.c = c
        self.pdh = c.WinDLL("pdh")
        self.pdh.PdhOpenQueryW.argtypes = [w.LPCWSTR, c.c_size_t, c.POINTER(c.c_void_p)]
        self.pdh.PdhAddEnglishCounterW.argtypes = [c.c_void_p, w.LPCWSTR, c.c_size_t, c.POINTER(c.c_void_p)]
        self.pdh.PdhCollectQueryData.argtypes = [c.c_void_p]
        self.pdh.PdhCloseQuery.argtypes = [c.c_void_p]
        class Value(c.Structure):
            _fields_ = [("status", w.DWORD), ("value", c.c_double)]
        class Item(c.Structure):
            _fields_ = [("name", w.LPWSTR), ("value", Value)]
        self.Item = Item
        self.pdh.PdhGetFormattedCounterArrayW.argtypes = [c.c_void_p, w.DWORD, c.POINTER(w.DWORD), c.POINTER(w.DWORD), c.c_void_p]
        query = c.c_void_p()
        if self.pdh.PdhOpenQueryW(None, 0, c.byref(query)) != 0:
            return
        self.query = query
        self.engine = c.c_void_p()
        self.memory = c.c_void_p()
        if self.pdh.PdhAddEnglishCounterW(query, r"\GPU Engine(*)\Utilization Percentage", 0, c.byref(self.engine)) != 0:
            self.close()
            return
        self.pdh.PdhAddEnglishCounterW(query, r"\GPU Adapter Memory(*)\Dedicated Usage", 0, c.byref(self.memory))
        self.pdh.PdhCollectQueryData(query)
        try:
            class Display(c.Structure):
                _fields_ = [("cb", w.DWORD), ("device", w.WCHAR * 32), ("description", w.WCHAR * 128),
                            ("flags", w.DWORD), ("device_id", w.WCHAR * 128), ("key", w.WCHAR * 128)]
            d = Display()
            d.cb = c.sizeof(d)
            if c.windll.user32.EnumDisplayDevicesW(None, 0, c.byref(d), 0):
                self.name = d.description or "GPU"
        except (OSError, AttributeError):
            pass

    def values(self, counter):
        from ctypes import wintypes as w
        c = self.c
        size, count = w.DWORD(), w.DWORD()
        self.pdh.PdhGetFormattedCounterArrayW(counter, 0x200, c.byref(size), c.byref(count), None)
        if not size.value or size.value > 16 * 1024 * 1024:
            return []
        buffer = c.create_string_buffer(size.value)
        if self.pdh.PdhGetFormattedCounterArrayW(counter, 0x200, c.byref(size), c.byref(count), buffer) != 0:
            return []
        items = c.cast(buffer, c.POINTER(self.Item))
        return [(items[i].name or "", items[i].value.value) for i in range(count.value) if items[i].value.status in (0, 1)]

    def sample(self):
        if not self.query or self.pdh.PdhCollectQueryData(self.query) != 0:
            return None, None
        import re
        groups = {}
        for name, value in self.values(self.engine):
            match = re.search(r"(luid_.*?_phys_\d+).*?engtype_(.*)", name)
            if match:
                identity = match.group(1) + ":" + match.group(2)
                groups[identity] = groups.get(identity, 0) + max(0, value)
        # Dedicated memory is meaningful for discrete GPUs. Integrated drivers
        # may report zero; do not label shared RAM as dedicated VRAM.
        memory = sum(max(0, v) for _, v in self.values(self.memory)) if self.memory else 0
        return min(100.0, max(groups.values())) if groups else None, int(memory) if memory else None

    def close(self):
        if self.query:
            self.pdh.PdhCloseQuery(self.query)
            self.query = None


class SystemStats(QObject):
    updated = Signal(object)
    processes_updated = Signal(object)

    def __init__(self, interval=1000):
        super().__init__()
        self.interval = interval
        self.timer = None
        self.last_time = 0.0
        self.last_network = None
        self.last_disk = None
        self.gpu = None
        self.processor = platform.processor() or "Processor"
        self.boot_time = None
        self.process_timer = None
        self.process_cache = {}

    @Slot()
    def start(self):
        if sys.platform == "win32":
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                    self.processor = winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
            except OSError:
                pass
        try:
            self.gpu = WindowsGPU()
        except (OSError, AttributeError, ValueError):
            self.gpu = None
        psutil.cpu_percent(None)
        try:
            self.boot_time = psutil.boot_time()
        except (OSError, psutil.Error, NotImplementedError):
            pass
        self.process_timer = QTimer(self)
        self.process_timer.setInterval(2000)
        self.process_timer.timeout.connect(self.sample_processes)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.sample)
        self.timer.start(self.interval)
        self.sample()

    @Slot(int)
    def set_interval(self, interval):
        self.interval = interval
        if self.timer:
            self.timer.setInterval(interval)

    @Slot()
    def stop(self):
        if self.timer:
            self.timer.stop()
        if self.process_timer:
            self.process_timer.stop()
        if self.gpu:
            self.gpu.close()

    @Slot(bool)
    def set_process_monitor(self, enabled):
        if not self.process_timer:
            return
        if enabled:
            self.process_cache.clear()
            self.sample_processes()
            self.process_timer.start()
        else:
            self.process_timer.stop()
            self.process_cache.clear()

    @Slot()
    def sample_processes(self):
        rows, cache = [], {}
        processors = psutil.cpu_count() or 1
        try:
            for process in psutil.process_iter(["pid", "name", "create_time"]):
                try:
                    identity = (process.pid, process.info["create_time"])
                    tracked = self.process_cache.get(identity, process)
                    with tracked.oneshot():
                        cpu = tracked.cpu_percent(None)
                        memory = tracked.memory_info().rss
                    rows.append({"pid": process.pid, "name": process.info["name"] or "Unknown",
                                 "cpu": min(100.0, cpu / processors) if identity in self.process_cache else None,
                                 "memory": memory})
                    cache[identity] = tracked
                except (psutil.Error, OSError):
                    continue
        except (psutil.Error, OSError):
            pass
        self.process_cache = cache
        self.processes_updated.emit(rows)

    @Slot()
    def sample(self):
        now = time.monotonic()
        elapsed = now - self.last_time if self.last_time else 0
        s = Snapshot(timestamp=now, processor=self.processor)
        if self.boot_time is not None:
            s.uptime_seconds = max(0, time.time() - self.boot_time)
        try:
            s.process_count = len(psutil.pids())
        except (OSError, psutil.Error):
            pass
        try:
            battery = psutil.sensors_battery()
            if battery:
                s.battery_percent, s.battery_plugged = battery.percent, battery.power_plugged
        except (OSError, psutil.Error, AttributeError, NotImplementedError):
            pass
        try:
            s.cpu = psutil.cpu_percent(None)
            frequency = psutil.cpu_freq()
            s.frequency_mhz = frequency.current if frequency and frequency.current > 0 else None
        except (OSError, psutil.Error, NotImplementedError):
            pass
        try:
            m = psutil.virtual_memory()
            s.memory_percent, s.memory_used, s.memory_total = m.percent, m.total - m.available, m.total
        except (OSError, psutil.Error):
            pass
        try:
            s.disk_percent = psutil.disk_usage("C:\\" if sys.platform == "win32" else "/").percent
        except (OSError, psutil.Error):
            pass
        for kind, reader, attributes in (
            ("network", psutil.net_io_counters, ("bytes_sent", "bytes_recv")),
            ("disk", psutil.disk_io_counters, ("read_bytes", "write_bytes")),
        ):
            try:
                current = reader()
                previous = getattr(self, "last_" + kind)
                rates = [max(0, getattr(current, attr) - getattr(previous, attr)) / elapsed for attr in attributes] if elapsed and current and previous else [None, None]
                if kind == "network":
                    s.upload, s.download = rates
                else:
                    s.read_rate, s.write_rate = rates
                setattr(self, "last_" + kind, current)
            except (OSError, psutil.Error, AttributeError):
                setattr(self, "last_" + kind, None)
        if self.gpu:
            try:
                s.gpu_percent, s.gpu_memory = self.gpu.sample()
                s.gpu_name = self.gpu.name
            except (OSError, ValueError, AttributeError):
                pass
        self.last_time = now
        self.updated.emit(s)
