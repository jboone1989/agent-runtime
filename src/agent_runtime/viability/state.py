"""Multi-dimensional viability — score is not a sole reward."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..config.loader import ViabilityConfig
from ..self_model.model import SelfState
from ..cognition.world_model import WorldState


@dataclass
class ViabilityDimension:
    dimension: str
    value: float
    preferred_min: float
    preferred_max: float
    critical_min: float
    critical_max: float
    trend: float
    uncertainty: float
    predicted_1h: float | None = None


@dataclass
class ViabilityState:
    dimensions: dict[str, ViabilityDimension]
    observed_at: datetime

    def get(self, name: str) -> ViabilityDimension:
        return self.dimensions[name]


@dataclass
class Threat:
    dimension: str
    severity: float
    description: str
    predicted_breach: bool


@dataclass
class Opportunity:
    dimension: str
    value: float
    description: str


@dataclass
class ViabilityAssessment:
    score: float
    threats: list[Threat]
    opportunities: list[Opportunity]
    urgency: float
    uncertainty: float
    dimensions: dict[str, ViabilityDimension] = field(default_factory=dict)


@dataclass
class ViabilityPrediction:
    horizon: timedelta
    predicted: ViabilityState
    uncertainty: float
