"""Canonical contract v1 DTOs. This repo is the source of truth.

VirtualBrain implements these shapes via adapter; it must not redefine them.
"""
from __future__ import annotations

from typing import Any

from ..motivation.engine import ActionTendency, MotivationState
from ..self_model.model import SelfModel

CONTRACT_VERSION = "1.0"

CONTRACT_ACTION_TO_KIND = {
    "REST": "conserve",
    "APPROACH": "maintain",
    "AVOID": "conserve",
    "EXPLORE": "introspect",
    "CONFRONT": "diagnose",
    "SEEK_SUPPORT": "record",
    "FREEZE": "noop",
}

KIND_TO_CONTRACT_DIRECTION = {
    "conserve": "REST",
    "maintain": "APPROACH",
    "introspect": "EXPLORE",
    "diagnose": "CONFRONT",
    "record": "SEEK_SUPPORT",
    "noop": "FREEZE",
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


def motivation_output(state: MotivationState) -> dict[str, Any]:
    tendencies = []
    for item in state.action_tendencies:
        payload: dict[str, Any] = {
            "direction": KIND_TO_CONTRACT_DIRECTION.get(item.action, "EXPLORE"),
            "strength": float(item.strength),
            "target": None if item.action in KIND_TO_CONTRACT_DIRECTION else item.action,
            "cause_refs": [],
        }
        tendencies.append(payload)
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


def action_tendencies_from_contract(items: list[dict[str, Any]]) -> list[ActionTendency]:
    out: list[ActionTendency] = []
    for item in items:
        direction = str(item.get("direction") or "EXPLORE")
        kind = CONTRACT_ACTION_TO_KIND.get(direction, "introspect")
        out.append(
            ActionTendency(
                action=kind,
                strength=float(item.get("strength") or 0.0),
                reason=f"contract:{direction}",
                dimension=None,
            )
        )
    return out
