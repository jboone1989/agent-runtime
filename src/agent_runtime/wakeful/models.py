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
class NeuralFeedbackEvent:
    outcome_id: str
    observed_at: datetime
    pathway: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    information_gain: float | None = None
    prediction_error: float | None = None
    goal_progress: float | None = None
    resource_cost: float | None = None
    social_result: float | None = None
    recovery_result: float | None = None

    def __post_init__(self) -> None:
        if not self.outcome_id or not self.pathway or not self.evidence_refs:
            raise ValueError("neural feedback requires outcome_id, pathway, and evidence refs")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("neural feedback observed_at must be timezone-aware")
        for name in ("information_gain", "prediction_error", "resource_cost"):
            value = getattr(self, name)
            if value is not None and not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"neural feedback {name} must be within [0, 1]")
        for name in ("goal_progress", "social_result", "recovery_result"):
            value = getattr(self, name)
            if value is not None and not -1.0 <= float(value) <= 1.0:
                raise ValueError(f"neural feedback {name} must be within [-1, 1]")
        signals = (
            self.information_gain, self.prediction_error, self.goal_progress,
            self.resource_cost, self.social_result, self.recovery_result,
        )
        if all(value is None for value in signals):
            raise ValueError("neural feedback requires at least one outcome signal")


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
    recent_neural_feedback: tuple[NeuralFeedbackEvent, ...] = field(default_factory=tuple)
