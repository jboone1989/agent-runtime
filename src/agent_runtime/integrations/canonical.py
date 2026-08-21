"""Canonical contract v1 DTOs. Protocol only — no tool or policy mapping."""
from __future__ import annotations

from typing import Any

from ..motivation.engine import MotivationState
from ..self_model.model import SelfModel

CONTRACT_VERSION = "1.0"
CONTRACT_DIRECTIONS = frozenset(
    {"APPROACH", "AVOID", "FREEZE", "EXPLORE", "SEEK_SUPPORT", "CONFRONT", "REST"}
)


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


def motivation_output(state: MotivationState) -> dict[str, Any]:
    tendencies: list[dict[str, Any]] = []
    for item in state.action_tendencies:
        direction = getattr(item, "direction", None)
        if direction not in CONTRACT_DIRECTIONS:
            continue
        tendencies.append(
            {
                "direction": direction,
                "strength": float(item.strength),
                "target": getattr(item, "target", None),
                "cause_refs": list(getattr(item, "cause_refs", None) or []),
            }
        )
    priority = list(state.attention_priority) or list(state.attention_bias.keys())
    return {
        "contract_version": CONTRACT_VERSION,
        "valence": float(state.valence),
        "arousal": float(state.arousal),
        "urgency": float(state.urgency),
        "threat": float(state.threat),
        "opportunity": float(state.opportunity),
        "drives": dict(state.drives),
        "attention_priority": priority,
        "action_tendencies": tendencies,
        "approach_avoid_bias": float(state.approach_avoid_bias),
        "exploration_bias": float(state.exploration_bias),
        "persistence": float(state.persistence),
        "risk_preference": float(state.risk_preference),
        "learning_signal": float(state.learning_signal),
        "self_model_updates": [
            {
                "path": u.path,
                "new_value": u.new_value,
                "reason": u.reason,
                "uncertainty": u.uncertainty,
                "evidence_refs": list(u.evidence_refs),
            }
            for u in state.self_model_updates
        ],
    }
