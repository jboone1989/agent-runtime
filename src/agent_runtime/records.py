"""Causal records kept from day one — even with no Agent Network attached."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ObservationRecord:
    record_id: str
    cycle: int
    timestamp: datetime
    observation_id: str
    source: str
    payload: dict[str, Any]
    external_task: bool


@dataclass
class MotivationRecord:
    record_id: str
    cycle: int
    timestamp: datetime
    valence: float
    arousal: float
    urgency: float
    threat: float
    opportunity: float
    drives: dict[str, float]
    learning_signal: float


@dataclass
class GoalCreated:
    record_id: str
    cycle: int
    timestamp: datetime
    goal_id: str
    origin: str
    description: str
    reason: str
    evidence: list[str]


@dataclass
class DecisionRecord:
    decision_id: str
    cycle: int
    timestamp: datetime
    goal_ids: list[str]
    proposed_actions: list[str]
    authorized_actions: list[str]
    denied: list[dict[str, Any]]
    reason: str
    viability_score: float
    motivation_urgency: float
    question: str = ""
    alternatives: list[str] = field(default_factory=list)
    context_refs: list[str] = field(default_factory=list)
    prediction: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.5
    reversal_conditions: list[str] = field(default_factory=list)


@dataclass
class ActionRecord:
    action_id: str
    cycle: int
    timestamp: datetime
    kind: str
    resource: str
    goal_id: str | None
    payload: dict[str, Any]
    compute_cost: float
    financial_cost: float
    trigger: str
    goal_origin: str | None
    reason: str
    evidence: list[str]
    decision_id: str | None = None


@dataclass
class OutcomeRecord:
    outcome_id: str
    action_id: str
    cycle: int
    timestamp: datetime
    success: bool
    description: str
    effects: dict[str, Any]
    evidence_result: str | None = None
    missing_evidence_units: list[str] = field(default_factory=list)


@dataclass
class ReflectionRecord:
    record_id: str
    cycle: int
    timestamp: datetime
    summary: str
    hypothesis: str | None
    evidence: list[str]


@dataclass
class RecordLog:
    observations: list[ObservationRecord] = field(default_factory=list)
    self_model_updates: list[Any] = field(default_factory=list)
    viability: list[Any] = field(default_factory=list)
    motivations: list[MotivationRecord] = field(default_factory=list)
    goals_created: list[GoalCreated] = field(default_factory=list)
    decisions: list[DecisionRecord] = field(default_factory=list)
    actions: list[ActionRecord] = field(default_factory=list)
    outcomes: list[OutcomeRecord] = field(default_factory=list)
    reflections: list[ReflectionRecord] = field(default_factory=list)
    regulations: list[Any] = field(default_factory=list)
    escalations: list[Any] = field(default_factory=list)
