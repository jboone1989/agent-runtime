from __future__ import annotations

from datetime import datetime, timezone

from agent_runtime.cognition.planner import SimplePlanner
from agent_runtime.goals.arbitration import arbitrate
from agent_runtime.goals.goal import Goal, GoalOrigin
from agent_runtime.motivation.engine import MotivationState
from agent_runtime.viability.state import ViabilityAssessment


NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


def _goal(description: str, origin: GoalOrigin, priority: float) -> Goal:
    return Goal.create(
        description=description,
        origin=origin,
        created_at=NOW,
        reason=description,
        priority=priority,
    )


def _motivation() -> MotivationState:
    return MotivationState.neutral()


def _viability() -> ViabilityAssessment:
    return ViabilityAssessment(
        score=1.0, threats=[], opportunities=[], urgency=0.0,
        uncertainty=0.0, dimensions={},
    )


def test_user_commitment_outranks_internal_self_improvement() -> None:
    internal = _goal("Evolve myself", GoalOrigin.INTERNAL, 0.95)
    user = _goal("Complete the user's real task", GoalOrigin.USER, 0.40)

    ranked = arbitrate([internal, user], [], _motivation(), _viability())

    assert user.priority == 0.86
    assert ranked[0].goal_id == user.goal_id


def test_generic_tendency_prefers_user_over_internal_goal() -> None:
    internal = _goal("Evolve myself", GoalOrigin.INTERNAL, 0.95)
    user = _goal("Complete the user's real task", GoalOrigin.USER, 0.86)
    motivation = _motivation()
    # A tendency without a matching dimension used to be captured by INTERNAL.
    from agent_runtime.motivation.engine import ActionTendency
    motivation.action_tendencies = [
        ActionTendency(action="maintain", strength=0.8, reason="approach", dimension=None)
    ]

    proposals = SimplePlanner().plan([user, internal], motivation)

    tendency = next(row for row in proposals if row.kind == "maintain")
    assert tendency.goal_id == user.goal_id
    assert tendency.goal_origin == "USER"
