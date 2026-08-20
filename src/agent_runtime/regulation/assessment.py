from __future__ import annotations

from dataclasses import dataclass, field

from ..sensing.signal import Signal


@dataclass
class RegulationAssessment:
    system: str
    severity: float
    summary: str
    signals: list[Signal] = field(default_factory=list)
