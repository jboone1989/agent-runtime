"""Simple priority arbitration. Viability maintenance outranks slack user work when urgent."""
from __future__ import annotations

from ..motivation.engine import MotivationState
from ..viability.state import ViabilityAssessment
from .goal import Goal, GoalOrigin, GoalStatus


def arbitrate(
    existing: list[Goal],
    generated: list[Goal],
    motivation: MotivationState,
    viability: ViabilityAssessment,
) -> list[Goal]:
    merged = list(existing)
    known = {g.goal_id for g in merged}
    for g in generated:
        if g.goal_id not in known:
            merged.append(g)
            known.add(g.goal_id)

    if viability.urgency >= 0.5:
        for g in merged:
            if g.origin == GoalOrigin.VIABILITY and g.status == GoalStatus.ACTIVE:
                g.priority = max(g.priority, 0.8 + 0.2 * motivation.urgency)
    return sorted(merged, key=lambda g: g.priority, reverse=True)
