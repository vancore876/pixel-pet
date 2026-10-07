"""Small, testable parachute motion independent of GUI painting."""
from dataclasses import dataclass
import math


@dataclass
class ParachuteMotion:
    y: float
    ground: float
    velocity: float = 25.0

    def step(self, elapsed: float):
        elapsed = max(0.0, min(0.25, elapsed))
        self.velocity = min(130.0, self.velocity + 110 * elapsed)
        self.y = min(self.ground, self.y + self.velocity * elapsed)
        return self.y >= self.ground


@dataclass
class HopMotion:
    start_x: float
    start_y: float
    end_x: float
    end_y: float
    duration: float = 1.0
    elapsed: float = 0.0

    def step(self, delta):
        if self.duration <= 0:
            return self.end_x, self.end_y, True
        self.elapsed = min(self.duration, self.elapsed + max(0, min(0.25, delta)))
        t = self.elapsed / self.duration
        if t >= 1:
            return self.end_x, self.end_y, True
        return (self.start_x + (self.end_x - self.start_x) * t,
                self.start_y + (self.end_y - self.start_y) * t - math.sin(math.pi * t) * 70,
                t >= 1)
