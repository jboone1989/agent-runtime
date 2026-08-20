"""Due-time collection. Sensors do not share a single frequency."""
from __future__ import annotations

from datetime import datetime

from .raw import RawObservation
from .registry import SensorRegistry
from .sensor import SensorContext


class SensorScheduler:
    def __init__(self) -> None:
        self._last: dict[str, datetime] = {}

    def collect(
        self,
        registry: SensorRegistry,
        context: SensorContext,
        now: datetime,
    ) -> list[RawObservation]:
        out: list[RawObservation] = []
        for sensor in registry.all():
            last = self._last.get(sensor.sensor_id)
            due = last is None or (now - last).total_seconds() >= sensor.interval_seconds
            if not due:
                continue
            out.extend(sensor.sample(context))
            self._last[sensor.sensor_id] = now
        return out
