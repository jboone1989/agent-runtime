from __future__ import annotations

from ..raw import RawObservation
from ..sensor import SensorContext


class MemorySensor:
    sensor_id = "memory_sensor"
    modality = "memory"
    interval_seconds = 10.0

    def sample(self, context: SensorContext) -> list[RawObservation]:
        mem = context.environment.read("memory") or {}
        return [
            RawObservation.create(
                sensor_id=self.sensor_id,
                modality=self.modality,
                timestamp=context.now,
                payload={
                    "retrieval_failure": float(mem.get("retrieval_failure", 0.0)),
                    "corruption": float(mem.get("corruption", 0.0)),
                    "write_failure": float(mem.get("write_failure", 0.0)),
                    "consistency": float(mem.get("consistency", 1.0)),
                    "read_latency": float(mem.get("read_latency", 0.05)),
                },
                metadata={"entity_id": "memory_store"},
            )
        ]
