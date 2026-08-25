"""Sensor observations. Self payload vs world payload stay namespaced."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from .clock import new_id


@dataclass
class Event:
    event_id: str
    kind: str
    description: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class StateDelta:
    self_updates: dict[str, Any] = field(default_factory=dict)
    world_updates: dict[str, Any] = field(default_factory=dict)
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class InterventionEvidence:
    intervention_id: str
    target_domain: Literal["SELF", "WORLD", "OTHER_AGENT", "CAPABILITY"]
    target: str
    operation: str
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class Observation:
    observation_id: str
    timestamp: datetime
    source: str
    payload: dict[str, Any]
    event: Event | None = None
    external_task: bool = False
    evidence_refs: list[str] = field(default_factory=list)
    intervention: InterventionEvidence | None = None

    @classmethod
    def self_and_world(
        cls,
        self_metrics: dict[str, Any],
        world: dict[str, Any] | None = None,
        *,
        source: str = "sensors",
        event: Event | None = None,
        timestamp: datetime | None = None,
        external_task: bool = False,
        evidence_refs: list[str] | None = None,
        self_uncertainty: dict[str, float] | None = None,
        world_uncertainty: dict[str, float] | None = None,
        intervention: InterventionEvidence | None = None,
    ) -> Observation:
        payload: dict[str, Any] = {
            "self": dict(self_metrics),
            "world": dict(world or {}),
        }
        if self_uncertainty or world_uncertainty:
            payload["uncertainty"] = {
                "self": dict(self_uncertainty or {}),
                "world": dict(world_uncertainty or {}),
            }
        return cls(
            observation_id=new_id("obs"),
            timestamp=timestamp or datetime.now(timezone.utc),
            source=source,
            payload=payload,
            event=event,
            external_task=external_task,
            evidence_refs=list(evidence_refs or []),
            intervention=intervention,
        )

    @classmethod
    def from_delta(
        cls,
        delta: StateDelta,
        *,
        timestamp: datetime | None = None,
        source: str = "sensors",
        external_task: bool = False,
    ) -> Observation:
        return cls.self_and_world(
            self_metrics=dict(delta.self_updates),
            world=dict(delta.world_updates),
            source=source,
            timestamp=timestamp,
            external_task=external_task,
            evidence_refs=list(delta.evidence_refs),
        )

    def self_payload(self) -> dict[str, Any]:
        raw = self.payload.get("self") or {}
        return dict(raw) if isinstance(raw, dict) else {}

    def world_payload(self) -> dict[str, Any]:
        raw = self.payload.get("world") or {}
        return dict(raw) if isinstance(raw, dict) else {}

    def uncertainty_for(self, domain: str, name: str) -> float | None:
        uncertainty = self.payload.get("uncertainty")
        if not isinstance(uncertainty, dict):
            return None
        domain_values = uncertainty.get(domain)
        if not isinstance(domain_values, dict):
            return None
        value = domain_values.get(name)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        return max(0.0, min(1.0, float(value)))
