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

    # Explicit human goals are commitments, not background suggestions.
    # Internal self-improvement may compete only below them unless a concrete
    # viability threat is urgent enough to preempt ordinary work.
    for g in merged:
        if g.origin == GoalOrigin.USER and g.status == GoalStatus.ACTIVE:
            g.priority = max(g.priority, 0.86)

    if viability.urgency >= 0.5:
        for g in merged:
            if g.origin == GoalOrigin.VIABILITY and g.status == GoalStatus.ACTIVE:
                g.priority = max(g.priority, 0.8 + 0.2 * motivation.urgency)

    urgent_viability = viability.urgency >= 0.5
    def commitment_tier(goal: Goal) -> int:
        if urgent_viability and goal.origin == GoalOrigin.VIABILITY:
            return 0
        if goal.origin == GoalOrigin.USER:
            return 1
        return 2

    return sorted(
        merged,
        key=lambda g: (commitment_tier(g), -g.priority, g.created_at, g.goal_id),
    )
