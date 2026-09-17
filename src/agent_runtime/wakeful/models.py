"""Provider-neutral state models for an always-awake low-cost runtime core."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class WakefulState(str, Enum):
    AWAKE = "AWAKE"
    THINKING = "THINKING"
    OBSERVING = "OBSERVING"
    ACTING = "ACTING"
    WAITING = "WAITING"
    DEGRADED = "DEGRADED"


@dataclass(frozen=True)
class StimulusEvent:
    event_id: str
    source: str
    kind: str
    observed_at: datetime
    salience: float
    novelty: float
    urgency: float
    persistence: float
    payload_ref: str | None = None
    attention_channel: str | None = None

    def __post_init__(self) -> None:
        if not self.event_id or not self.source or not self.kind:
            raise ValueError("stimulus identity/source/kind are required")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("stimulus observed_at must be timezone-aware")
        for name in ("salience", "novelty", "urgency", "persistence"):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"stimulus {name} must be between 0 and 1")


@dataclass(frozen=True)
class PlannedWake:
    wake_id: str
    at: datetime
    reason: str
    task: str
    relevant_refs: tuple[str, ...] = ()
    cancel_conditions: tuple[str, ...] = ()
    preemptible: bool = True

    def __post_init__(self) -> None:
        if self.at.tzinfo is None or self.at.utcoffset() is None:
            raise ValueError("planned wake time must be timezone-aware")
        if not self.wake_id or not self.reason or not self.task:
            raise ValueError("planned wake identity/reason/task are required")


@dataclass(frozen=True)
class AttentionSignal:
    channel: str
    value: float
    threshold_crossed: bool


@dataclass(frozen=True)
class CognitionTrigger:
    trigger_id: str
    created_at: datetime
    channel: str
    source_event_id: str
    priority: float
    reason: str


@dataclass(frozen=True)
class NeuralControlSignal:
    source_event_id: str
    neuron: str
    kind: str
    weight: float
    intent: str | None = None

    def __post_init__(self) -> None:
        if not self.source_event_id or not self.neuron or not self.kind:
            raise ValueError("neural control signal identity is required")
        if not 0.0 <= float(self.weight) <= 1.0:
            raise ValueError("neural control signal weight must be between 0 and 1")


@dataclass(frozen=True)
class WakeReason:
    kind: str
    detail: str
    event_id: str | None = None
    wake_id: str | None = None
    task: str | None = None
    cognition_required: bool = False


@dataclass(frozen=True)
class WakefulSnapshot:
    observed_at: datetime
    state: WakefulState
    next_wake_at: datetime | None
    next_wake_reason: str | None
    pending_plans: tuple[PlannedWake, ...]
    recent_stimuli: tuple[StimulusEvent, ...]
    attention: dict[str, float]
    resource_pressure: str
    active_goal_refs: tuple[str, ...] = ()
    open_inquiry_refs: tuple[str, ...] = ()
    last_cognition_at: datetime | None = None
    last_action_at: datetime | None = None
    service_started_at: datetime | None = None
    cognition_triggers: tuple[CognitionTrigger, ...] = field(default_factory=tuple)
    neural_state: dict = field(default_factory=dict)
    neural_control_signals: tuple[NeuralControlSignal, ...] = field(default_factory=tuple)
