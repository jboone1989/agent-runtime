from __future__ import annotations

from ..sensor import SensorContext
from ..signal import Signal


class ProviderSensor:
    name = "provider_sensor"
    interval_seconds = 30.0

    def sense(self, context: SensorContext) -> list[Signal]:
        now = context.now
        active = context.providers.active
        stats = context.world.providers.get(active) or {
            "error_rate": 0.02,
            "latency_ms": 80.0,
            "available": True,
            "rate_limited": False,
        }
        error_rate = float(stats.get("error_rate", 0.0))
        latency = float(stats.get("latency_ms", 0.0))
        available = bool(stats.get("available", True))
        limited = bool(stats.get("rate_limited", False))
        return [
            Signal.create(
                source=self.name,
                domain="cognition",
                kind="PROVIDER_ERROR_RATE",
                value=error_rate,
                severity=error_rate,
                confidence=0.95,
                scope="SELF",
                timestamp=now,
                evidence_refs=[f"provider:{active}"],
            ),
            Signal.create(
                source=self.name,
                domain="cognition",
                kind="PROVIDER_LATENCY",
                value=latency,
                severity=min(1.0, max(0.0, (latency - 200.0) / 2000.0)),
                confidence=0.9,
                scope="SELF",
                timestamp=now,
            ),
            Signal.create(
                source=self.name,
                domain="cognition",
                kind="PROVIDER_AVAILABLE",
                value=available,
                severity=0.0 if available else 1.0,
                confidence=0.95,
                scope="SELF",
                timestamp=now,
            ),
            Signal.create(
                source=self.name,
                domain="cognition",
                kind="PROVIDER_RATE_LIMIT",
                value=limited,
                severity=0.6 if limited else 0.0,
                confidence=0.9,
                scope="SELF",
                timestamp=now,
            ),
        ]
