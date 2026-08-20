"""Turn signals + local outcomes + escalations into an Observation. Sensors never write SelfModel."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ..observations import Observation
from ..sensing.signal import Signal
from ..sensing.world import SenseWorld
from ..survival.failover import ProviderPool
from .action import RegulatoryAction
from .escalation import Escalation

KIND_TO_METRIC = {
    "COMPUTE": ("compute", "value"),
    "DISK": ("storage", "value"),
    "FINANCIAL": ("financial_resources", "value"),
    "TOKEN_BUDGET": ("financial_resources", "value"),
    "MEMORY_USAGE": ("memory_integrity", "value"),
    "PROVIDER_ERROR_RATE": ("llm_error_rate", "value"),
    "WORKER_HEALTH": ("operational_integrity", "value"),
    "CONSISTENCY": ("memory_integrity", "value"),
}


def build_observation(
    *,
    signals: list[Signal],
    escalations: list[Escalation],
    local_actions: list[RegulatoryAction],
    world: SenseWorld,
    providers: ProviderPool,
    timestamp: datetime,
) -> Observation:
    self_metrics: dict[str, Any] = {
        "compute": float(world.resources.get("compute", 0.8)),
        "storage": float(world.resources.get("disk", 0.8)),
        "financial_resources": float(world.resources.get("financial", 0.7)),
        "memory_integrity": max(0.0, 1.0 - float(world.memory.get("corruption", 0.0))),
        "operational_integrity": 0.9 if world.process.get("alive", True) else 0.2,
        "capability": 0.8,
        "security": 0.85,
        "identity_integrity": 0.95,
        "network_connectivity": 0.9,
        "social_support": 0.5,
        "goal_progress": 0.6,
        "future_survivability": 0.7,
        "llm_error_rate": 0.02,
        "active_provider": providers.active,
    }
    world_payload: dict[str, Any] = {}
    for signal in signals:
        if signal.scope == "WORLD":
            world_payload[signal.kind.lower()] = signal.value
            continue
        mapped = KIND_TO_METRIC.get(signal.kind)
        if mapped and isinstance(signal.value, (int, float)):
            self_metrics[mapped[0]] = float(signal.value)
        if signal.kind == "RETRIEVAL_FAILURE" and isinstance(signal.value, (int, float)):
            self_metrics["memory_integrity"] = max(
                0.0, min(self_metrics["memory_integrity"], 1.0 - float(signal.value))
            )
        if signal.kind == "CONFIG_MUTATION" and signal.value:
            self_metrics["security"] = min(self_metrics["security"], 0.25)
        if signal.kind == "CREDENTIAL_INVALID" and signal.value:
            self_metrics["security"] = min(self_metrics["security"], 0.2)
        if signal.kind == "PROVIDER_ERROR_RATE" and isinstance(signal.value, (int, float)):
            self_metrics["capability"] = max(0.0, 1.0 - float(signal.value))

    local_kinds = {a.kind for a in local_actions}
    if "switch_provider" in local_kinds:
        stats = world.providers.get(providers.active) or {}
        err = float(stats.get("error_rate", 0.02))
        self_metrics["llm_error_rate"] = err
        self_metrics["capability"] = 0.85 if err < 0.5 else 0.25
        self_metrics["active_provider"] = providers.active
        self_metrics["operational_integrity"] = 0.85
    if "contain" in local_kinds or "restore_config" in local_kinds:
        self_metrics["security"] = max(self_metrics.get("security", 0.5), 0.45)

    for esc in escalations:
        dim = esc.suggested_dimension or "capability"
        self_metrics[dim] = min(float(self_metrics.get(dim, 0.8)), 0.18)

    return Observation.self_and_world(
        self_metrics=self_metrics,
        world=world_payload,
        timestamp=timestamp,
        external_task=False,
    )
