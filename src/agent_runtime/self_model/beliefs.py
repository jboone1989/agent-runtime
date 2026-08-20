"""Structured beliefs. Uncertainty is first-class."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


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
