"""Turn measurements into percepts. Severity/threat live in SignalEncoder, not here."""
from __future__ import annotations

from ..sensing.raw import RawObservation
from .base import PerceptionContext
from .percept import Percept


def _payload(obs: RawObservation) -> dict:
    return obs.payload if isinstance(obs.payload, dict) else {}


class DigitalPerception:
    def perceive(
        self, observations: list[RawObservation], context: PerceptionContext
    ) -> list[Percept]:
        out: list[Percept] = []
        for obs in observations:
            if obs.modality == "resource":
                out.append(self._resource(obs))
            elif obs.modality == "provider":
                out.extend(self._provider(obs))
            elif obs.modality == "integrity":
                out.extend(self._integrity(obs))
            elif obs.modality == "process":
                out.extend(self._process(obs))
            elif obs.modality == "memory":
                out.extend(self._memory(obs))
        return out

    def _resource(self, obs: RawObservation) -> Percept:
        data = _payload(obs)
        entity = str(data.get("channel") or obs.metadata.get("entity_id") or "resource")
        remaining = float(data.get("remaining", 0.0))
        return Percept.from_raw(
            observation_id=obs.observation_id,
            modality=obs.modality,
            kind="RESOURCE_LEVEL",
            timestamp=obs.timestamp,
            entity_id=entity,
            attributes={"remaining": remaining, "channel": entity},
            confidence=obs.quality,
            salience=max(0.0, 0.5 - remaining),
            metadata={"sensor_id": obs.sensor_id},
        )

    def _provider(self, obs: RawObservation) -> list[Percept]:
        data = _payload(obs)
        name = str(data.get("provider") or obs.metadata.get("entity_id") or "provider")
        return [
            Percept.from_raw(
                observation_id=obs.observation_id,
                modality=obs.modality,
                kind="PROVIDER_STATUS",
                timestamp=obs.timestamp,
                entity_id=name,
                attributes={
                    "error_rate": float(data.get("error_rate", 0.0)),
                    "latency_ms": float(data.get("latency_ms", 0.0)),
                    "available": bool(data.get("available", True)),
                    "rate_limited": bool(data.get("rate_limited", False)),
                    "active": bool(data.get("active", False)),
                },
                confidence=obs.quality,
                salience=float(data.get("error_rate", 0.0)),
                metadata={"sensor_id": obs.sensor_id},
            )
        ]

    def _integrity(self, obs: RawObservation) -> list[Percept]:
        data = _payload(obs)
        items = [
            ("CONFIG_STATE", "runtime_config", {"mutated": bool(data.get("config_mutated", False))}),
            (
                "CREDENTIAL_STATE",
                "credentials",
                {"valid": bool(data.get("credentials_valid", True))},
            ),
            ("SIGNATURE_STATE", "runtime_config", {"ok": bool(data.get("signature_ok", True))}),
            (
                "DEPENDENCY_HASH_STATE",
                "dependency_tree",
                {"ok": bool(data.get("dependency_hash_ok", True))},
            ),
            ("AUTH_STATE", "auth_gateway", {"anomaly": bool(data.get("auth_anomaly", False))}),
        ]
        out: list[Percept] = []
        for kind, entity, attrs in items:
            out.append(
                Percept.from_raw(
                    observation_id=obs.observation_id,
                    modality=obs.modality,
                    kind=kind,
                    timestamp=obs.timestamp,
                    entity_id=entity,
                    attributes={**attrs, "restore_blocked": bool(data.get("restore_blocked", False))},
                    confidence=obs.quality,
                    salience=0.5,
                    metadata={"sensor_id": obs.sensor_id},
                )
            )
        return out

    def _process(self, obs: RawObservation) -> list[Percept]:
        data = _payload(obs)
        return [
            Percept.from_raw(
                observation_id=obs.observation_id,
                modality=obs.modality,
                kind="PROCESS_STATUS",
                timestamp=obs.timestamp,
                entity_id="runtime_process",
                attributes=dict(data),
                confidence=obs.quality,
                salience=0.0 if data.get("alive", True) else 1.0,
                metadata={"sensor_id": obs.sensor_id},
            )
        ]

    def _memory(self, obs: RawObservation) -> list[Percept]:
        data = _payload(obs)
        return [
            Percept.from_raw(
                observation_id=obs.observation_id,
                modality=obs.modality,
                kind="MEMORY_STATUS",
                timestamp=obs.timestamp,
                entity_id="memory_store",
                attributes=dict(data),
                confidence=obs.quality,
                salience=float(data.get("corruption", 0.0)),
                metadata={"sensor_id": obs.sensor_id},
            )
        ]


class PhysicalPerception:
    def perceive(
        self, observations: list[RawObservation], context: PerceptionContext
    ) -> list[Percept]:
        out: list[Percept] = []
        for obs in observations:
            data = _payload(obs)
            if obs.modality == "energy":
                remaining = float(data.get("remaining", 0.0))
                out.append(
                    Percept.from_raw(
                        observation_id=obs.observation_id,
                        modality=obs.modality,
                        kind="RESOURCE_LEVEL",
                        timestamp=obs.timestamp,
                        entity_id="battery",
                        attributes={"remaining": remaining, "channel": "battery"},
                        confidence=obs.quality,
                        salience=max(0.0, 0.5 - remaining),
                        metadata={"sensor_id": obs.sensor_id},
                    )
                )
            elif obs.modality == "proximity":
                distance = float(data.get("distance", 1.0))
                out.append(
                    Percept.from_raw(
                        observation_id=obs.observation_id,
                        modality=obs.modality,
                        kind="PROXIMITY",
                        timestamp=obs.timestamp,
                        entity_id="range_finder",
                        attributes={"distance": distance},
                        confidence=obs.quality,
                        salience=max(0.0, 1.0 - distance),
                        metadata={"sensor_id": obs.sensor_id},
                    )
                )
            elif obs.modality == "thermal":
                temp = float(data.get("value", 0.0))
                out.append(
                    Percept.from_raw(
                        observation_id=obs.observation_id,
                        modality=obs.modality,
                        kind="THERMAL_LEVEL",
                        timestamp=obs.timestamp,
                        entity_id="thermal_body",
                        attributes={"value": temp},
                        confidence=obs.quality,
                        salience=max(0.0, temp - 0.6),
                        metadata={"sensor_id": obs.sensor_id},
                    )
                )
        return out
