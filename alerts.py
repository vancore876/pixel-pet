"""Load alerts based on sustained activity, with independent warning cooldowns."""
from __future__ import annotations


class LoadAlerts:
    def __init__(self):
        self.cpu_since = None
        self.last_cpu = self.last_ram = self.last_notice = -float("inf")

    def evaluate(self, cpu, ram, settings, now: float, notify: bool = True):
        if cpu is not None and cpu >= settings["cpu_alert"]:
            if self.cpu_since is None:
                self.cpu_since = now
        else:
            self.cpu_since = None
        if not notify or now - self.last_notice < 20:
            return None
        cooldown = settings["alert_cooldown"]
        if self.cpu_since is not None and now - self.cpu_since >= settings["alert_duration"] and now - self.last_cpu >= cooldown:
            self.last_cpu = self.last_notice = now
            return "CPU working hard!"
        if ram is not None and ram >= settings["ram_alert"] and now - self.last_ram >= cooldown:
            self.last_ram = self.last_notice = now
            return "Memory getting full!"
        return None
