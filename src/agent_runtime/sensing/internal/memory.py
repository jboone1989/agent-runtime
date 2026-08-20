from __future__ import annotations

from ..sensor import SensorContext
from ..signal import Signal


class MemorySensor:
    name = "memory_sensor"
    interval_seconds = 10.0

    def sense(self, context: SensorContext) -> list[Signal]:
        now = context.now
        mem = context.world.memory
        retrieval = float(mem.get("retrieval_failure", 0.0))
        corruption = float(mem.get("corruption", 0.0))
        write_fail = float(mem.get("write_failure", 0.0))
        consistency = float(mem.get("consistency", 1.0))
        latency = float(mem.get("read_latency", 0.05))
        return [
            Signal.create(
                source=self.name,
                domain="memory",
                kind="RETRIEVAL_FAILURE",
                value=retrieval,
                severity=retrieval,
                confidence=0.85,
                scope="SELF",
                timestamp=now,
            ),
            Signal.create(
                source=self.name,
                domain="memory",
                kind="CORRUPTION",
                value=corruption,
                severity=corruption,
                confidence=0.8,
                scope="SELF",
                timestamp=now,
            ),
            Signal.create(
                source=self.name,
                domain="memory",
                kind="WRITE_FAILURE",
                value=write_fail,
                severity=write_fail,
                confidence=0.9,
                scope="SELF",
                timestamp=now,
            ),
            Signal.create(
                source=self.name,
                domain="memory",
                kind="CONSISTENCY",
                value=consistency,
                severity=max(0.0, 1.0 - consistency),
                confidence=0.8,
                scope="SELF",
                timestamp=now,
            ),
            Signal.create(
                source=self.name,
                domain="memory",
                kind="READ_LATENCY",
                value=latency,
                severity=min(1.0, max(0.0, (latency - 0.2) / 1.0)),
                confidence=0.7,
                scope="SELF",
                timestamp=now,
            ),
        ]
