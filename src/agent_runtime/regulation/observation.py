"""Turn signals + local outcomes + escalations into a delta Observation.

Unobserved fields are omitted. Sensors never write SelfModel.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ..observations import Observation, StateDelta
from ..sensing.signal import Signal
from .action import RegulatoryAction
from .escalation import Escalation

KIND_TO_METRIC = {
    "COMPUTE": "compute",
    "DISK": "storage",
    "FINANCIAL": "financial_resources",
    "TOKEN_BUDGET": "financial_resources",
    "MEMORY_USAGE": "memory_integrity",
    "BATTERY": "energy",
    "PROVIDER_ERROR_RATE": "llm_error_rate",
    "WORKER_HEALTH": "operational_integrity",
    "CONSISTENCY": "memory_integrity",
}


def build_delta(
    *,
    signals: list[Signal],
    escalations: list[Escalation],
    local_actions: list[RegulatoryAction],
) -> StateDelta:
    self_updates: dict[str, Any] = {}
    world_updates: dict[str, Any] = {}
    evidence: list[str] = []
    for signal in signals:
        evidence.append(signal.signal_id)
        if signal.scope == "WORLD":
            world_updates[signal.kind.lower()] = signal.value
            continue
        mapped = KIND_TO_METRIC.get(signal.kind)
        if mapped and isinstance(signal.value, (int, float)):
            self_updates[mapped] = float(signal.value)
        if signal.kind == "RETRIEVAL_FAILURE" and isinstance(signal.value, (int, float)):
            self_updates["memory_integrity"] = max(
                0.0, min(self_updates.get("memory_integrity", 1.0), 1.0 - float(signal.value))
            )
        if signal.kind == "CONFIG_MUTATION" and signal.value:
            self_updates["security"] = min(self_updates.get("security", 1.0), 0.25)
        if signal.kind == "CREDENTIAL_INVALID" and signal.value:
            self_updates["security"] = min(self_updates.get("security", 1.0), 0.2)
        if signal.kind == "PROVIDER_ERROR_RATE" and isinstance(signal.value, (int, float)):
            if signal.metadata.get("active", True):
                self_updates["capability"] = max(0.0, 1.0 - float(signal.value))
                self_updates["llm_error_rate"] = float(signal.value)
        if signal.kind == "PROVIDER_ERROR_RATE" and signal.entity_id and signal.metadata.get("active"):
            self_updates["active_provider"] = signal.entity_id

    local_kinds = {a.kind for a in local_actions}
    if "switch_provider" in local_kinds:
        self_updates["operational_integrity"] = 0.85
    if "contain" in local_kinds or "restore_config" in local_kinds:
        if "security" in self_updates:
            self_updates["security"] = max(self_updates["security"], 0.45)

    for esc in escalations:
        dim = esc.suggested_dimension
        if dim:
            self_updates[dim] = min(float(self_updates.get(dim, 0.8)), 0.18)

    return StateDelta(
        self_updates=self_updates,
        world_updates=world_updates,
        evidence_refs=evidence,
    )


def build_observation(
    *,
    signals: list[Signal],
    escalations: list[Escalation],
    local_actions: list[RegulatoryAction],
    timestamp: datetime,
    environment=None,
    providers=None,
    world=None,
) -> Observation:
    delta = build_delta(
        signals=signals, escalations=escalations, local_actions=local_actions
    )
    return Observation.from_delta(delta, timestamp=timestamp)
