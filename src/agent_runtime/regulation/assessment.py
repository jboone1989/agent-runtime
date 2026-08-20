from __future__ import annotations

from dataclasses import dataclass, field

from ..sensing.signal import Signal


@dataclass
class CausalHypothesis:
    cause: str
    effect: str
    confidence: float


@dataclass
class RegulationAssessment:
    system: str
    severity: float
    summary: str
    signals: list[Signal] = field(default_factory=list)
    affected_entities: list[str] = field(default_factory=list)
    affected_components: list[str] = field(default_factory=list)
    causal_hypotheses: list[CausalHypothesis] = field(default_factory=list)
