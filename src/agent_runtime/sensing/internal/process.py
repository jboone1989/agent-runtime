from __future__ import annotations

from ..sensor import SensorContext
from ..signal import Signal


class ProcessSensor:
    name = "process_sensor"
    interval_seconds = 5.0

    def sense(self, context: SensorContext) -> list[Signal]:
        now = context.now
        proc = context.world.process
        alive = bool(proc.get("alive", True))
        crashes = int(proc.get("crash_count", 0))
        backlog = int(proc.get("queue_backlog", 0))
        health = float(proc.get("worker_health", 1.0))
        return [
            Signal.create(
                source=self.name,
                domain="process",
                kind="RUNTIME_ALIVE",
                value=alive,
                severity=0.0 if alive else 1.0,
                confidence=1.0,
                scope="SELF",
                timestamp=now,
            ),
            Signal.create(
                source=self.name,
                domain="process",
                kind="WORKER_HEALTH",
                value=health,
                severity=max(0.0, 1.0 - health),
                confidence=0.85,
                scope="SELF",
                timestamp=now,
            ),
            Signal.create(
                source=self.name,
                domain="process",
                kind="QUEUE_BACKLOG",
                value=backlog,
                severity=min(1.0, backlog / 20.0),
                confidence=0.9,
                scope="SELF",
                timestamp=now,
            ),
            Signal.create(
                source=self.name,
                domain="process",
                kind="CRASH_COUNT",
                value=crashes,
                severity=min(1.0, crashes / 5.0),
                confidence=1.0,
                scope="SELF",
                timestamp=now,
            ),
        ]
