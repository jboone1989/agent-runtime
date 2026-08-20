from __future__ import annotations

from ..raw import RawObservation
from ..sensor import SensorContext


class ProviderSensor:
    sensor_id = "provider_sensor"
    modality = "provider"
    interval_seconds = 30.0

    def sample(self, context: SensorContext) -> list[RawObservation]:
        providers = context.environment.read("providers") or {}
        active = context.environment.read("active_provider")
        out: list[RawObservation] = []
        for name, stats in providers.items():
            if not isinstance(stats, dict):
                continue
            out.append(
                RawObservation.create(
                    sensor_id=self.sensor_id,
                    modality=self.modality,
                    timestamp=context.now,
                    payload={
                        "provider": name,
                        "error_rate": float(stats.get("error_rate", 0.0)),
                        "latency_ms": float(stats.get("latency_ms", 0.0)),
                        "available": bool(stats.get("available", True)),
                        "rate_limited": bool(stats.get("rate_limited", False)),
                        "active": name == active,
                    },
                    metadata={"entity_id": name},
                )
            )
        return out
