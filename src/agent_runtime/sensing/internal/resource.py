from __future__ import annotations

from ..sensor import SensorContext
from ..signal import Signal


class ResourceSensor:
    name = "resource_sensor"
    interval_seconds = 5.0

    def sense(self, context: SensorContext) -> list[Signal]:
        now = context.now
        res = context.world.resources
        out: list[Signal] = []
        mapping = {
            "compute": "COMPUTE",
            "memory": "MEMORY_USAGE",
            "disk": "DISK",
            "token_budget": "TOKEN_BUDGET",
            "financial": "FINANCIAL",
        }
        for key, kind in mapping.items():
            value = float(res.get(key, 0.8))
            shortage = max(0.0, 0.5 - value) / 0.5
            out.append(
                Signal.create(
                    source=self.name,
                    domain="resource",
                    kind=kind,
                    value=value,
                    severity=shortage,
                    confidence=0.9,
                    scope="SELF",
                    timestamp=now,
                )
            )
        return out
