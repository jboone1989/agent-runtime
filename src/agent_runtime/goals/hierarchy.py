from __future__ import annotations

from .goal import Goal, GoalOrigin, GoalStatus


def active_goals(goals: list[Goal]) -> list[Goal]:
    return [g for g in goals if g.status == GoalStatus.ACTIVE]


def by_origin(goals: list[Goal], origin: GoalOrigin) -> list[Goal]:
    return [g for g in goals if g.origin == origin]
