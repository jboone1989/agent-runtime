from __future__ import annotations

from typing import Protocol

from .sensor import SensorContext
from .signal import Signal


class Sensor(Protocol):
    name: str
    interval_seconds: float

    def sense(self, context: SensorContext) -> list[Signal]: ...


class SensorRegistry:
    def __init__(self) -> None:
        self._sensors: dict[str, Sensor] = {}

    def register(self, sensor: Sensor) -> None:
        self._sensors[sensor.name] = sensor

    def all(self) -> list[Sensor]:
        return list(self._sensors.values())
