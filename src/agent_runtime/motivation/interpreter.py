"""TendencyInterpreter: contract direction → TendencyEffect.

Compatible ActionTendency.action values are temporary SimplePlanner
mappings (SEEK_SUPPORT → record is not help-seeking). CONTRACT_ACTION_TO_KIND
stays in legacy_tendency_interpreter.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..cognition.world_model import WorldState
from ..integrations.canonical import ContractActionTendency
from ..integrations.legacy_tendency_interpreter import CONTRACT_ACTION_TO_KIND
from ..self_model.model import SelfModel
from .engine import ActionTendency

# Temporary compatibility deltas. Not attention control, not Executive.
_RISK_DOWN = -0.2
_EXPLORATION_DOWN = -0.2
_EXPLORATION_UP = 0.2


@dataclass
class TendencyEffect:
    risk_bias: float = 0.0
    exploration_delta: float = 0.0
    goal_priority_delta: dict[str, float] = field(default_factory=dict)
    planning_constraints: list[str] = field(default_factory=list)
    candidate_subgoals: list[str] = field(default_factory=list)
    executable_tendencies: list[ActionTendency] = field(default_factory=list)


class TendencyInterpreter(Protocol):
    def interpret(
        self,
        tendency: ContractActionTendency,
        *,
        self_model: SelfModel,
        world_state: WorldState,
        goals: list,
        available_capabilities: dict,
    ) -> TendencyEffect: ...


class DefaultTendencyInterpreter:
    """Embodiment-agnostic mapping. goals and available_capabilities unused this slice."""

    def interpret(
        self,
        tendency: ContractActionTendency,
        *,
        self_model: SelfModel,
        world_state: WorldState,
        goals: list,
        available_capabilities: dict,
    ) -> TendencyEffect:
        _ = (self_model, world_state, goals, available_capabilities)
        kind = CONTRACT_ACTION_TO_KIND[tendency.direction]
        risk_bias = 0.0
        exploration_delta = 0.0
        if tendency.direction in {"REST", "AVOID"}:
            risk_bias = _RISK_DOWN
            exploration_delta = _EXPLORATION_DOWN
        elif tendency.direction == "EXPLORE":
            exploration_delta = _EXPLORATION_UP
        elif tendency.direction == "FREEZE":
            exploration_delta = _EXPLORATION_DOWN
        return TendencyEffect(
            risk_bias=risk_bias,
            exploration_delta=exploration_delta,
            executable_tendencies=[
                ActionTendency(
                    action=kind,
                    strength=float(tendency.strength),
                    reason=f"temporary compatibility:{tendency.direction}",
                )
            ],
        )
