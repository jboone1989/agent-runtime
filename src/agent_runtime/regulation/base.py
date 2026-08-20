from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..sensing.signal import Signal
from .action import RegulatoryAction, RegulatoryStateUpdate
from .assessment import RegulationAssessment
from .context import RegulationContext
from .escalation import Escalation


@dataclass
class RegulationDecision:
    local_actions: list[RegulatoryAction] = field(default_factory=list)
    escalations: list[Escalation] = field(default_factory=list)
    state_updates: list[RegulatoryStateUpdate] = field(default_factory=list)
    reason: str = ""
    confidence: float = 0.5
    system: str = ""


class RegulatorySystem(Protocol):
    name: str

    def accepts(self, signal: Signal) -> bool: ...

    def assess(
        self,
        signals: list[Signal],
        context: RegulationContext,
    ) -> RegulationAssessment: ...

    def respond(
        self,
        assessment: RegulationAssessment,
        context: RegulationContext,
    ) -> RegulationDecision: ...
