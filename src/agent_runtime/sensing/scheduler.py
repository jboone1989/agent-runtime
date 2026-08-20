"""Due-time collection. Sensors do not share a single frequency."""
from __future__ import annotations

from datetime import datetime

from .registry import Sensor, SensorRegistry
from .sensor import SensorContext
from .signal import Signal


class SensorScheduler:
    def __init__(self) -> None:
        self._last: dict[str, datetime] = {}

    def collect(
        self,
        registry: SensorRegistry,
        context: SensorContext,
        now: datetime,
    ) -> list[Signal]:
        out: list[Signal] = []
        for sensor in registry.all():
            last = self._last.get(sensor.name)
            due = last is None or (now - last).total_seconds() >= sensor.interval_seconds
            if not due:
                continue
            out.extend(sensor.sense(context))
            self._last[sensor.name] = now
        return out
