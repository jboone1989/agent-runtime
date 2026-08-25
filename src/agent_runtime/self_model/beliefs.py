"""Structured beliefs. Uncertainty is first-class."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class BeliefRevisionStatus(str, Enum):
    OPEN = "OPEN"
    CORRECTED = "CORRECTED"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


@dataclass
class IdentityBelief:
    agent_id: str
    genesis_id: str
    version: str
    uncertainty: float = 0.0


@dataclass
class CapabilityBelief:
    name: str
    available: bool
    proficiency: float
    uncertainty: float
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class ResourceBelief:
    name: str
    amount: float
    unit: str
    uncertainty: float
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class VulnerabilityBelief:
    name: str
    severity: float
    description: str
    uncertainty: float
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class StateBelief:
    name: str
    value: Any
    trend: float
    uncertainty: float
    last_updated: datetime
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class RelationshipBelief:
    other_id: str
    kind: str
    trust: float
    uncertainty: float
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class SelfPrediction:
    horizon_hours: float
    description: str
    predicted: dict[str, Any]
    confidence: float
    prediction_id: str = ""
    target_path: str = ""
    baseline: Any = None
    intervention: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    evidence_refs: list[str] = field(default_factory=list)
    status: str = "OPEN"
    observed: Any = None
    error: float | None = None


@dataclass
class BeliefRevision:
    revision_id: str
    path: str
    prior_value: Any
    observed_value: Any
    accepted_value: Any
    status: BeliefRevisionStatus
    timestamp: datetime
    prior_uncertainty: float
    observation_uncertainty: float
    evidence_refs: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class SelfMonitoringEvent:
    event_id: str
    path: str
    prior_value: Any
    observed_value: Any
    materiality: float
    spontaneous: bool
    timestamp: datetime
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class KnowledgeAssessment:
    assessment_id: str
    query: str
    value: Any
    answerable: bool
    confidence: float
    timestamp: datetime
    basis: list[str] = field(default_factory=list)
    resolved_correct: bool | None = None
    calibration_error: float | None = None
    resolution_evidence: list[str] = field(default_factory=list)
