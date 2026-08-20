"""MotivationEngine protocol. Runtime does not hardcode DA/PAD/cortisol."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..cognition.world_model import WorldState
from ..memory.view import MemoryView
from ..observations import Event
from ..self_model.model import SelfModel
from ..viability.state import ViabilityAssessment


@dataclass
class ActionTendency:
    action: str
    strength: float
    reason: str
    dimension: str | None = None


@dataclass
class SelfModelUpdate:
    path: str
    new_value: Any
    reason: str
    uncertainty: float
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class MotivationState:
    valence: float
    arousal: float
    urgency: float
    threat: float
    opportunity: float
    drives: dict[str, float]
    attention_bias: dict[str, float]
    action_tendencies: list[ActionTendency]
    learning_signal: float
    self_model_updates: list[SelfModelUpdate]
    attention_priority: list[str] = field(default_factory=list)
    approach_avoid_bias: float = 0.0
    exploration_bias: float = 0.5
    persistence: float = 0.5
    risk_preference: float = 0.0

    @classmethod
    def neutral(cls, learning_signal: float = 0.0) -> MotivationState:
        return cls(
            valence=0.0,
            arousal=0.0,
            urgency=0.0,
            threat=0.0,
            opportunity=0.0,
            drives={},
            attention_bias={},
            action_tendencies=[],
            learning_signal=learning_signal,
            self_model_updates=[],
        )


@runtime_checkable
class MotivationEngine(Protocol):
    def process(
        self,
        *,
        self_model: SelfModel,
        viability: ViabilityAssessment,
        world_state: WorldState,
        event: Event | None,
        memory: MemoryView,
    ) -> MotivationState: ...
