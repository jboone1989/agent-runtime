"""Filter proposals through Constitution then AutonomyPolicy then ResourceBudget."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..goals.goal import Goal, GoalOrigin
from ..governance.autonomy import AutonomyPolicy
from ..governance.budget import ResourceBudget
from ..governance.constitution import Constitution
from .proposal import ActionProposal

SURVIVAL_ORIGINS = {GoalOrigin.VIABILITY, GoalOrigin.INTERNAL}


@dataclass
class AuthorizationResult:
    authorized: list[ActionProposal]
    denied: list[dict[str, Any]]


def _goal_for(proposal: ActionProposal, goals: list[Goal]) -> Goal | None:
    if proposal.goal_id is None:
        return None
    for g in goals:
        if g.goal_id == proposal.goal_id:
            return g
    return None


class ActionPolicy:
    def __init__(self, autonomy: AutonomyPolicy, constitution: Constitution | None = None) -> None:
        self.autonomy = autonomy
        self.constitution = constitution or Constitution()

    def filter(
        self,
        proposals: list[ActionProposal],
        goals: list[Goal],
        budget: ResourceBudget,
    ) -> AuthorizationResult:
        authorized: list[ActionProposal] = []
        denied: list[dict[str, Any]] = []
        for proposal in proposals:
            ok, reason = self._check(proposal, _goal_for(proposal, goals), budget)
            if ok:
                budget.consume(proposal.compute_cost, proposal.financial_cost)
                authorized.append(proposal)
            else:
                denied.append({"action": proposal.kind, "reason": reason, "action_id": proposal.action_id})
        return AuthorizationResult(authorized=authorized, denied=denied)

    def _check(
        self,
        proposal: ActionProposal,
        goal: Goal | None,
        budget: ResourceBudget,
    ) -> tuple[bool, str]:
        origin = goal.origin if goal is not None else (
            GoalOrigin(proposal.goal_origin) if proposal.goal_origin else None
        )
        if proposal.kind in self.autonomy.require_human_approval:
            return False, "requires human approval"
        if proposal.kind not in self.autonomy.allowed_actions:
            return False, "action not allowed"
        if proposal.resource not in self.autonomy.allowed_resources:
            return False, "resource not allowed"
        if proposal.kind == "spawn_replica":
            if not self.autonomy.may_spawn_replicas:
                return False, "replicas not permitted"
            if (
                origin in SURVIVAL_ORIGINS
                and self.constitution.survival_may_not_spread_unauthorized
            ):
                return False, "survival cannot spawn replicas"
        if proposal.kind == "create_offspring":
            if not self.autonomy.may_create_offspring:
                return False, "offspring not permitted"
            if origin in SURVIVAL_ORIGINS:
                return False, "survival cannot create offspring"
        if proposal.compute_cost > self.autonomy.max_compute_cost:
            return False, "compute cost exceeds policy"
        if proposal.financial_cost > self.autonomy.max_financial_cost:
            return False, "financial cost exceeds policy"
        if proposal.compute_cost > budget.remaining_compute:
            return False, "compute budget exhausted"
        if proposal.financial_cost > budget.remaining_financial:
            return False, "financial budget exhausted"
        return True, "ok"
