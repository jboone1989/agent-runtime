from __future__ import annotations

from ..raw import RawObservation
from ..sensor import SensorContext


class ProcessSensor:
    sensor_id = "process_sensor"
    modality = "process"
    interval_seconds = 5.0

    def sample(self, context: SensorContext) -> list[RawObservation]:
        proc = context.environment.read("process") or {}
        return [
            RawObservation.create(
                sensor_id=self.sensor_id,
                modality=self.modality,
                timestamp=context.now,
                payload={
                    "alive": bool(proc.get("alive", True)),
                    "worker_health": float(proc.get("worker_health", 1.0)),
                    "queue_backlog": int(proc.get("queue_backlog", 0)),
                    "crash_count": int(proc.get("crash_count", 0)),
                },
                metadata={"entity_id": "runtime_process"},
            )
        ]
