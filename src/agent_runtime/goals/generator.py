"""Emit Internal/Viability goals from viability threats — no user prompt required."""
from __future__ import annotations

from datetime import datetime

from ..viability.state import ViabilityAssessment
from .goal import Goal, GoalOrigin, GoalStatus


class GoalGenerator:
    def __init__(self, threat_threshold: float = 0.25) -> None:
        self.threat_threshold = threat_threshold

    def generate(
        self,
        viability: ViabilityAssessment,
        existing: list[Goal],
        now: datetime,
        evidence: list[str],
    ) -> list[Goal]:
        created: list[Goal] = []
        active = [g for g in existing if g.status == GoalStatus.ACTIVE]
        for threat in viability.threats:
            if threat.severity < self.threat_threshold:
                continue
            description = f"Restore {threat.dimension} into preferred range"
            if any(g.description == description and g.origin == GoalOrigin.VIABILITY for g in active):
                continue
            created.append(
                Goal.create(
                    description=description,
                    origin=GoalOrigin.VIABILITY,
                    created_at=now,
                    reason=threat.description,
                    priority=min(1.0, 0.5 + threat.severity),
                    evidence_refs=list(evidence),
                    dimension=threat.dimension,
                    success_criteria=f"{threat.dimension} back in preferred band",
                )
            )
        return created
