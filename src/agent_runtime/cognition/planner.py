"""Deterministic planner. v0.1 does not call an LLM."""
from __future__ import annotations

from ..action.proposal import ActionProposal
from ..goals.goal import Goal, GoalStatus
from ..motivation.engine import MotivationState


class SimplePlanner:
    def plan(self, goals: list[Goal], motivation: MotivationState) -> list[ActionProposal]:
        active = [g for g in goals if g.status == GoalStatus.ACTIVE]
        if not active and not motivation.action_tendencies:
            return [
                ActionProposal(
                    kind="noop",
                    resource="self",
                    compute_cost=0.0,
                    financial_cost=0.0,
                    goal_id=None,
                    reason="idle",
                    trigger="INTERNAL",
                )
            ]
        proposals: list[ActionProposal] = []
        used_kinds: set[tuple[str, str | None]] = set()
        for tendency in motivation.action_tendencies:
            goal = _match_goal(active, tendency.dimension)
            key = (tendency.action, tendency.dimension)
            if key in used_kinds:
                continue
            used_kinds.add(key)
            proposals.append(
                ActionProposal(
                    kind=tendency.action,
                    resource="self" if tendency.action != "record" else "memory",
                    compute_cost=0.05 if tendency.action != "spawn_replica" else 0.2,
                    financial_cost=0.0,
                    goal_id=None if goal is None else goal.goal_id,
                    reason=tendency.reason,
                    payload={"dimension": tendency.dimension} if tendency.dimension else {},
                    trigger="INTERNAL",
                    goal_origin=None if goal is None else goal.origin.value,
                    evidence=[] if goal is None else list(goal.evidence_refs),
                )
            )
        for goal in active:
            if goal.origin.value == "USER" and not any(p.goal_id == goal.goal_id for p in proposals):
                proposals.append(
                    ActionProposal(
                        kind="introspect",
                        resource="self",
                        compute_cost=0.02,
                        financial_cost=0.0,
                        goal_id=goal.goal_id,
                        reason=goal.reason,
                        trigger="USER",
                        goal_origin=goal.origin.value,
                        evidence=list(goal.evidence_refs),
                    )
                )
        return proposals or [
            ActionProposal(
                kind="introspect",
                resource="self",
                compute_cost=0.02,
                financial_cost=0.0,
                goal_id=None if not active else active[0].goal_id,
                reason="default introspect",
                trigger="INTERNAL",
                goal_origin=None if not active else active[0].origin.value,
            )
        ]


def _match_goal(goals: list[Goal], dimension: str | None) -> Goal | None:
    if dimension:
        for g in goals:
            if g.dimension == dimension:
                return g
    for g in goals:
        if g.origin.value in {"VIABILITY", "INTERNAL"}:
            return g
    return goals[0] if goals else None
