"""Defensive sensing of the agent's own environment. No external scanning."""
from __future__ import annotations

from ..raw import RawObservation
from ..sensor import SensorContext


class SecuritySensor:
    sensor_id = "security_sensor"
    modality = "integrity"
    interval_seconds = 10.0

    def sample(self, context: SensorContext) -> list[RawObservation]:
        sec = context.environment.read("security") or {}
        return [
            RawObservation.create(
                sensor_id=self.sensor_id,
                modality=self.modality,
                timestamp=context.now,
                payload={
                    "config_mutated": bool(sec.get("config_mutated", False)),
                    "credentials_valid": bool(sec.get("credentials_valid", True)),
                    "signature_ok": bool(sec.get("signature_ok", True)),
                    "dependency_hash_ok": bool(sec.get("dependency_hash_ok", True)),
                    "auth_anomaly": bool(sec.get("auth_anomaly", False)),
                    "restore_blocked": bool(sec.get("restore_blocked", False)),
                },
                metadata={"entity_id": "runtime_config"},
            )
        ]
