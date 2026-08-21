"""Canonical contract v1 DTOs. Protocol only — no tool or policy mapping."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from ..motivation.engine import SelfModelUpdate
from ..self_model.model import SelfModel

CONTRACT_VERSION = "1.0"
CONTRACT_DIRECTIONS = frozenset(
    {"APPROACH", "AVOID", "FREEZE", "EXPLORE", "SEEK_SUPPORT", "CONFRONT", "REST"}
)

ContractDirection = Literal[
    "APPROACH",
    "AVOID",
    "FREEZE",
    "EXPLORE",
    "SEEK_SUPPORT",
    "CONFRONT",
    "REST",
]


@dataclass
class ContractActionTendency:
    """Contract-level motivation/behavior tendency (canonical semantics).

    Deliberately distinct from Runtime's ``ActionTendency``: the contract
    expresses *direction of motivation*; Runtime expresses *executable
    action candidates*. Converting between the two requires an explicit
    adapter (see ``legacy_tendency_interpreter``), never this module.
    """

    direction: ContractDirection
    strength: float
    target: str | None = None
    cause_refs: list[str] = field(default_factory=list)


def self_state_view(self_model: SelfModel, *, agent_id: str) -> dict[str, Any]:
    metrics: dict[str, float] = {}
    for name, belief in self_model.internal_state.items():
        if isinstance(getattr(belief, "value", None), (int, float)):
            metrics[name] = float(belief.value)
    capabilities: dict[str, Any] = {}
    for name, cap in self_model.capabilities.items():
        capabilities[name] = {
            "available": bool(getattr(cap, "available", False)),
            "proficiency": float(getattr(cap, "proficiency", 0.0) or 0.0),
        }
    uncertainty: dict[str, float] = {}
    for key, value in self_model.uncertainty.items():
        if isinstance(value, (int, float)):
            uncertainty[key] = float(value)
    return {
        "contract_version": CONTRACT_VERSION,
        "agent_id": agent_id,
        "metrics": metrics,
        "capabilities": capabilities,
        "uncertainty": uncertainty,
        "extensions": {},
    }


def body_state_view(self_model: SelfModel) -> dict[str, Any]:
    body = self_model.body
    components: list[dict[str, Any]] = []
    if body is not None:
        for component in body.components.values():
            relation = body.relation_of(component.component_id)
            components.append(
                {
                    "component_id": component.component_id,
                    "kind": component.kind,
                    "health": float(component.health),
                    "relation": relation.value if hasattr(relation, "value") else str(relation),
                }
            )
    sensed: dict[str, float] = {}
    for name, belief in self_model.internal_state.items():
        if isinstance(getattr(belief, "value", None), (int, float)):
            sensed[name] = float(belief.value)
    return {
        "contract_version": CONTRACT_VERSION,
        "components": components,
        "sensed_channels": sensed,
        "inferred_channels": {},
        "extensions": {},
    }


def world_state_view(world_state) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "environment": dict(getattr(world_state, "environment", None) or {}),
        "other_agents": dict(getattr(world_state, "other_agents", None) or {}),
        "extensions": {},
    }


def viability_view(viability) -> dict[str, float]:
    dims = getattr(viability, "dimensions", None) or {}
    out: dict[str, float] = {}
    for name, dim in dims.items():
        value = getattr(dim, "value", dim)
        if isinstance(value, (int, float)):
            out[name] = float(value)
    if not out and hasattr(viability, "score"):
        out["score"] = float(viability.score)
    return out


def recent_event_view(event) -> dict[str, Any]:
    if event is None:
        return {}
    return {
        "kind": getattr(event, "kind", ""),
        "description": getattr(event, "description", ""),
        "data": dict(getattr(event, "data", None) or {}),
    }


def motivation_input(
    *,
    self_model: SelfModel,
    agent_id: str,
    world_state,
    viability,
    event=None,
) -> dict[str, Any]:
    events = []
    if event is not None:
        events.append(recent_event_view(event))
    return {
        "contract_version": CONTRACT_VERSION,
        "self_state": self_state_view(self_model, agent_id=agent_id),
        "body_state": body_state_view(self_model),
        "world_state": world_state_view(world_state),
        "viability": viability_view(viability),
        "recent_events": events,
    }


def motivation_output(
    *,
    valence: float,
    arousal: float,
    urgency: float,
    threat: float,
    opportunity: float,
    drives: dict[str, float],
    attention_priority: list[str],
    action_tendencies: list[ContractActionTendency],
    approach_avoid_bias: float,
    exploration_bias: float,
    persistence: float,
    risk_preference: float,
    learning_signal: float,
    self_model_updates: list[SelfModelUpdate],
) -> dict[str, Any]:
    """Serialize canonical semantics only.

    Accepts explicit scalar/contract arguments — never a Runtime
    ``MotivationState``. Interpreting Runtime tendencies (action names like
    ``introspect``) as contract directions is an adapter's job, not this
    serializer's. Data is never dropped silently: unknown types raise
    ``TypeError``, unknown directions raise ``ValueError``.
    """
    tendencies: list[dict[str, Any]] = []
    for tendency in action_tendencies:
        if not isinstance(tendency, ContractActionTendency):
            raise TypeError(
                "action_tendencies items must be ContractActionTendency; "
                "Runtime ActionTendency requires an explicit adapter, "
                f"got {type(tendency).__name__}"
            )
        if tendency.direction not in CONTRACT_DIRECTIONS:
            raise ValueError(f"unknown contract direction: {tendency.direction!r}")
        tendencies.append(
            {
                "direction": tendency.direction,
                "strength": float(tendency.strength),
                "target": tendency.target,
                "cause_refs": list(tendency.cause_refs),
            }
        )
    return {
        "contract_version": CONTRACT_VERSION,
        "valence": float(valence),
        "arousal": float(arousal),
        "urgency": float(urgency),
        "threat": float(threat),
        "opportunity": float(opportunity),
        "drives": dict(drives),
        "attention_priority": list(attention_priority),
        "action_tendencies": tendencies,
        "approach_avoid_bias": float(approach_avoid_bias),
        "exploration_bias": float(exploration_bias),
        "persistence": float(persistence),
        "risk_preference": float(risk_preference),
        "learning_signal": float(learning_signal),
        "self_model_updates": [
            {
                "path": u.path,
                "new_value": u.new_value,
                "reason": u.reason,
                "uncertainty": u.uncertainty,
                "evidence_refs": list(u.evidence_refs),
            }
            for u in self_model_updates
        ],
    }
