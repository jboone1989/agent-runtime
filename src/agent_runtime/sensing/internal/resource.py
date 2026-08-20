from __future__ import annotations

from ..raw import RawObservation
from ..sensor import SensorContext


class ResourceSensor:
    sensor_id = "resource_sensor"
    modality = "resource"
    interval_seconds = 5.0

    def sample(self, context: SensorContext) -> list[RawObservation]:
        resources = context.environment.read("resources") or {}
        out: list[RawObservation] = []
        for key in ("compute", "memory", "disk", "token_budget", "financial"):
            if key not in resources:
                continue
            out.append(
                RawObservation.create(
                    sensor_id=self.sensor_id,
                    modality=self.modality,
                    timestamp=context.now,
                    payload={"channel": key, "remaining": float(resources[key])},
                    metadata={"entity_id": key},
                )
            )
        return out
