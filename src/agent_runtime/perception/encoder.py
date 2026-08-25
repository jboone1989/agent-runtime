"""Percept → Signal. Severity/threat belong here, not on Sensor."""
from __future__ import annotations

from ..sensing.signal import Signal
from .percept import Percept

RESOURCE_KIND = {
    "compute": "COMPUTE",
    # This value is remaining capacity, not integrity or usage percentage.
    "memory": "MEMORY_CAPACITY",
    "disk": "DISK",
    "token_budget": "TOKEN_BUDGET",
    "financial": "FINANCIAL",
    "battery": "BATTERY",
    "network": "NETWORK",
}


def _shortage(remaining: float, healthy: float = 0.5) -> float:
    return max(0.0, min(1.0, (healthy - remaining) / healthy))


def encode_builtin(percept: Percept, refs: list[str]) -> list[Signal]:
    source = str(percept.metadata.get("sensor_id") or percept.modality)
    ts = percept.timestamp
    entity = percept.entity_id
    attrs = percept.attributes
    conf = percept.confidence
    if percept.kind == "RESOURCE_LEVEL":
        channel = str(attrs.get("channel") or entity or "compute")
        remaining = float(attrs.get("remaining", 0.0))
        kind = RESOURCE_KIND.get(channel, channel.upper())
        return [
            Signal.create(
                source=source,
                domain="resource",
                kind=kind,
                value=remaining,
                severity=_shortage(remaining),
                confidence=conf,
                scope="SELF",
                timestamp=ts,
                evidence_refs=refs,
                entity_id=entity,
            )
        ]
    if percept.kind == "PROVIDER_STATUS":
        err = float(attrs.get("error_rate", 0.0))
        latency = float(attrs.get("latency_ms", 0.0))
        available = bool(attrs.get("available", True))
        limited = bool(attrs.get("rate_limited", False))
        return [
            Signal.create(
                source=source,
                domain="cognition",
                kind="PROVIDER_ERROR_RATE",
                value=err,
                severity=err,
                confidence=conf,
                scope="SELF",
                timestamp=ts,
                evidence_refs=refs,
                entity_id=entity,
                metadata={"active": bool(attrs.get("active", False))},
            ),
            Signal.create(
                source=source,
                domain="cognition",
                kind="PROVIDER_LATENCY",
                value=latency,
                severity=min(1.0, max(0.0, (latency - 200.0) / 2000.0)),
                confidence=conf,
                scope="SELF",
                timestamp=ts,
                evidence_refs=refs,
                entity_id=entity,
            ),
            Signal.create(
                source=source,
                domain="cognition",
                kind="PROVIDER_AVAILABLE",
                value=available,
                severity=0.0 if available else 1.0,
                confidence=conf,
                scope="SELF",
                timestamp=ts,
                evidence_refs=refs,
                entity_id=entity,
            ),
            Signal.create(
                source=source,
                domain="cognition",
                kind="PROVIDER_RATE_LIMIT",
                value=limited,
                severity=0.6 if limited else 0.0,
                confidence=conf,
                scope="SELF",
                timestamp=ts,
                evidence_refs=refs,
                entity_id=entity,
            ),
        ]
    if percept.kind == "CONFIG_STATE":
        mutated = bool(attrs.get("mutated", False))
        return [
            Signal.create(
                source=source,
                domain="integrity",
                kind="CONFIG_MUTATION",
                value=mutated,
                severity=0.85 if mutated else 0.0,
                confidence=conf,
                scope="SELF",
                timestamp=ts,
                evidence_refs=refs,
                entity_id=entity,
                metadata={"restore_blocked": bool(attrs.get("restore_blocked", False))},
            )
        ]
    if percept.kind == "CREDENTIAL_STATE":
        valid = bool(attrs.get("valid", True))
        return [
            Signal.create(
                source=source,
                domain="integrity",
                kind="CREDENTIAL_INVALID",
                value=not valid,
                severity=0.9 if not valid else 0.0,
                confidence=conf,
                scope="SELF",
                timestamp=ts,
                evidence_refs=refs,
                entity_id=entity,
            )
        ]
    if percept.kind == "SIGNATURE_STATE":
        ok = bool(attrs.get("ok", True))
        return [
            Signal.create(
                source=source,
                domain="integrity",
                kind="SIGNATURE_FAILURE",
                value=not ok,
                severity=0.8 if not ok else 0.0,
                confidence=conf,
                scope="SELF",
                timestamp=ts,
                evidence_refs=refs,
                entity_id=entity,
            )
        ]
    if percept.kind == "DEPENDENCY_HASH_STATE":
        ok = bool(attrs.get("ok", True))
        return [
            Signal.create(
                source=source,
                domain="integrity",
                kind="DEPENDENCY_HASH",
                value=not ok,
                severity=0.7 if not ok else 0.0,
                confidence=conf,
                scope="SELF",
                timestamp=ts,
                evidence_refs=refs,
                entity_id=entity,
            )
        ]
    if percept.kind == "AUTH_STATE":
        anomaly = bool(attrs.get("anomaly", False))
        return [
            Signal.create(
                source=source,
                domain="integrity",
                kind="AUTH_ANOMALY",
                value=anomaly,
                severity=0.6 if anomaly else 0.0,
                confidence=conf,
                scope="SELF",
                timestamp=ts,
                evidence_refs=refs,
                entity_id=entity,
            )
        ]
    if percept.kind == "PROCESS_STATUS":
        alive = bool(attrs.get("alive", True))
        health = float(attrs.get("worker_health", 1.0))
        backlog = int(attrs.get("queue_backlog", 0))
        crashes = int(attrs.get("crash_count", 0))
        return [
            Signal.create(
                source=source,
                domain="process",
                kind="RUNTIME_ALIVE",
                value=alive,
                severity=0.0 if alive else 1.0,
                confidence=conf,
                scope="SELF",
                timestamp=ts,
                evidence_refs=refs,
                entity_id=entity,
            ),
            Signal.create(
                source=source,
                domain="process",
                kind="WORKER_HEALTH",
                value=health,
                severity=max(0.0, 1.0 - health),
                confidence=conf,
                scope="SELF",
                timestamp=ts,
                evidence_refs=refs,
                entity_id=entity,
            ),
            Signal.create(
                source=source,
                domain="process",
                kind="QUEUE_BACKLOG",
                value=backlog,
                severity=min(1.0, backlog / 20.0),
                confidence=conf,
                scope="SELF",
                timestamp=ts,
                evidence_refs=refs,
                entity_id=entity,
            ),
            Signal.create(
                source=source,
                domain="process",
                kind="CRASH_COUNT",
                value=crashes,
                severity=min(1.0, crashes / 5.0),
                confidence=conf,
                scope="SELF",
                timestamp=ts,
                evidence_refs=refs,
                entity_id=entity,
            ),
        ]
    if percept.kind == "MEMORY_STATUS":
        retrieval = float(attrs.get("retrieval_failure", 0.0))
        corruption = float(attrs.get("corruption", 0.0))
        write_fail = float(attrs.get("write_failure", 0.0))
        consistency = float(attrs.get("consistency", 1.0))
        latency = float(attrs.get("read_latency", 0.05))
        return [
            Signal.create(
                source=source,
                domain="memory",
                kind="RETRIEVAL_FAILURE",
                value=retrieval,
                severity=retrieval,
                confidence=conf,
                scope="SELF",
                timestamp=ts,
                evidence_refs=refs,
                entity_id=entity,
            ),
            Signal.create(
                source=source,
                domain="memory",
                kind="CORRUPTION",
                value=corruption,
                severity=corruption,
                confidence=conf,
                scope="SELF",
                timestamp=ts,
                evidence_refs=refs,
                entity_id=entity,
            ),
            Signal.create(
                source=source,
                domain="memory",
                kind="WRITE_FAILURE",
                value=write_fail,
                severity=write_fail,
                confidence=conf,
                scope="SELF",
                timestamp=ts,
                evidence_refs=refs,
                entity_id=entity,
            ),
            Signal.create(
                source=source,
                domain="memory",
                kind="CONSISTENCY",
                value=consistency,
                severity=max(0.0, 1.0 - consistency),
                confidence=conf,
                scope="SELF",
                timestamp=ts,
                evidence_refs=refs,
                entity_id=entity,
            ),
            Signal.create(
                source=source,
                domain="memory",
                kind="READ_LATENCY",
                value=latency,
                severity=min(1.0, max(0.0, (latency - 0.2) / 1.0)),
                confidence=conf,
                scope="SELF",
                timestamp=ts,
                evidence_refs=refs,
                entity_id=entity,
            ),
        ]
    if percept.kind == "PROXIMITY":
        distance = float(attrs.get("distance", 1.0))
        return [
            Signal.create(
                source=source,
                domain="world",
                kind="DISTANCE",
                value=distance,
                severity=max(0.0, 1.0 - distance),
                confidence=conf,
                scope="WORLD",
                timestamp=ts,
                evidence_refs=refs,
                entity_id=entity,
            )
        ]
    if percept.kind == "THERMAL_LEVEL":
        temp = float(attrs.get("value", 0.0))
        return [
            Signal.create(
                source=source,
                domain="resource",
                kind="TEMPERATURE",
                value=temp,
                severity=max(0.0, min(1.0, (temp - 0.6) / 0.4)),
                confidence=conf,
                scope="SELF",
                timestamp=ts,
                evidence_refs=refs,
                entity_id=entity,
            )
        ]
    return []


class SignalEncoder:
    def __init__(self, mappers: list | None = None) -> None:
        if mappers is None:
            from .mapper import default_mappers

            mappers = default_mappers()
        self._mappers = list(mappers)

    def register(self, mapper) -> None:
        self._mappers.append(mapper)

    def encode(self, percepts: list[Percept]) -> list[Signal]:
        out: list[Signal] = []
        for percept in percepts:
            for mapper in self._mappers:
                if mapper.accepts(percept):
                    out.extend(mapper.encode(percept))
        return out

