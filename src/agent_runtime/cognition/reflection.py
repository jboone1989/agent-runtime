"""v0.1 reflection is a structured record, not fine-tuning."""
from __future__ import annotations

from datetime import datetime

from ..clock import new_id
from ..records import OutcomeRecord, ReflectionRecord
from ..viability.state import ViabilityAssessment


def reflect(
    *,
    cycle: int,
    timestamp: datetime,
    viability: ViabilityAssessment,
    outcomes: list[OutcomeRecord],
) -> ReflectionRecord:
    failed = [o for o in outcomes if not o.success]
    summary = (
        f"cycle {cycle}: viability_score={viability.score:.3f} "
        f"threats={len(viability.threats)} outcomes={len(outcomes)} failed={len(failed)}"
    )
    hypothesis = None
    if viability.threats:
        top = viability.threats[0]
        hypothesis = f"restoring {top.dimension} should reduce urgency"
    return ReflectionRecord(
        record_id=new_id("ref"),
        cycle=cycle,
        timestamp=timestamp,
        summary=summary,
        hypothesis=hypothesis,
        evidence=[t.dimension for t in viability.threats],
    )
