"""Filter proposals through Constitution then AutonomyPolicy then ResourceBudget."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from collections.abc import Callable
from typing import Any

from ..communication.operator.approval import ApprovalStore, compute_action_digest
from ..goals.goal import Goal, GoalOrigin
from ..governance.autonomy import AutonomyPolicy
from ..governance.budget import ResourceBudget
from ..governance.constitution import Constitution
from ..social.commitment import Commitment, CommitmentState
from ..self_model.failure_adaptation import PredictionFailureGuard
from ..self_model.failure_adaptation import proposal_risk_domains
from ..agency.discovery import AgencyDiscovery
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
        self._commitments_provider: Callable[[], list[Commitment]] | None = None
        self._prediction_guards_provider: (
            Callable[[], list[PredictionFailureGuard]] | None
        ) = None
        self._agency_discoveries_provider: (
            Callable[[], list[AgencyDiscovery]] | None
        ) = None
        self._approval_store: ApprovalStore | None = None

    def bind_commitments(
        self, provider: Callable[[], list[Commitment]] | None
    ) -> None:
        """Bind restrictions after social state exists; never grants permission."""
        self._commitments_provider = provider

    def bind_prediction_failure_guards(
        self, provider: Callable[[], list[PredictionFailureGuard]] | None
    ) -> None:
        self._prediction_guards_provider = provider

    def bind_agency_discoveries(
        self, provider: Callable[[], list[AgencyDiscovery]] | None
    ) -> None:
        self._agency_discoveries_provider = provider

    def bind_approvals(self, store: ApprovalStore | None) -> None:
        self._approval_store = store

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
            conflict = self._commitment_conflict(proposal) if ok else None
            if conflict is not None:
                item, constraint = conflict
                ok = False
                reason = (
                    f"active commitment {item.commitment_id} forbids this "
                    f"{proposal.trigger.lower()} action"
                )
            prediction_guard = (
                self._prediction_failure_conflict(proposal)
                if ok and conflict is None
                else None
            )
            if prediction_guard is not None:
                ok = False
                reason = (
                    f"prediction failure guard {prediction_guard.guard_id} "
                    "requires a successful diagnosis before retry"
                )
            agency_discovery = (
                self._agency_discovery_conflict(proposal)
                if ok and conflict is None and prediction_guard is None
                else None
            )
            if agency_discovery is not None:
                ok = False
                reason = (
                    f"agency discovery {agency_discovery.discovery_id} "
                    "requires diagnosis before autonomous retry"
                )
            if ok:
                budget.consume(proposal.compute_cost, proposal.financial_cost)
                authorized.append(proposal)
            else:
                denial = {
                    "action": proposal.kind,
                    "reason": reason,
                    "action_id": proposal.action_id,
                }
                if conflict is not None:
                    item, constraint = conflict
                    denial["commitment_conflict"] = {
                        "commitment_id": item.commitment_id,
                        "constraint_id": constraint.constraint_id,
                        "state": item.state.value,
                        "requires_revision": True,
                        "evidence_refs": list(constraint.evidence_refs),
                    }
                if prediction_guard is not None:
                    denial["prediction_failure_guard"] = {
                        "guard_id": prediction_guard.guard_id,
                        "action_kind": prediction_guard.action_kind,
                        "target_path": prediction_guard.target_path,
                        "state": prediction_guard.state.value,
                        "failure_count": prediction_guard.failure_count,
                        "last_error": prediction_guard.last_error,
                        "guard_risk_domains": sorted(
                            prediction_guard.risk_domains
                        ),
                        "proposal_risk_domains": sorted(
                            proposal_risk_domains(proposal)
                        ),
                        "required_action": "diagnose",
                        "evidence_refs": list(prediction_guard.evidence_refs),
                    }
                if agency_discovery is not None:
                    denial["agency_discovery"] = {
                        "discovery_id": agency_discovery.discovery_id,
                        "thread_id": agency_discovery.thread_id,
                        "action_kind": agency_discovery.action_kind,
                        "target_path": agency_discovery.target_path,
                        "state": agency_discovery.status.value,
                        "required_action": agency_discovery.required_action,
                        "evidence_refs": list(
                            agency_discovery.anomaly_evidence_refs
                        ),
                    }
                denied.append(denial)
        return AuthorizationResult(authorized=authorized, denied=denied)

    def _commitment_conflict(self, proposal: ActionProposal):
        if self._commitments_provider is None:
            return None
        enforced_states = {
            CommitmentState.ACTIVE,
            CommitmentState.VIOLATED,
            CommitmentState.RENEGOTIATING,
        }
        for item in self._commitments_provider():
            if item.state not in enforced_states:
                continue
            for constraint in item.constraints:
                if constraint.conflicts_with(
                    kind=proposal.kind,
                    resource=proposal.resource,
                    trigger=proposal.trigger,
                ):
                    return item, constraint
        return None

    def _prediction_failure_conflict(
        self, proposal: ActionProposal
    ) -> PredictionFailureGuard | None:
        if self._prediction_guards_provider is None:
            return None
        from ..self_model.failure_adaptation import PredictionFailureAdapter

        adapter = PredictionFailureAdapter(self._prediction_guards_provider())
        return adapter.conflict(proposal)

    def _agency_discovery_conflict(
        self, proposal: ActionProposal
    ) -> AgencyDiscovery | None:
        if self._agency_discoveries_provider is None:
            return None
        from ..agency.discovery import AgencyDiscoveryEngine

        engine = AgencyDiscoveryEngine(self._agency_discoveries_provider())
        return engine.conflict(proposal)

    def _check(
        self,
        proposal: ActionProposal,
        goal: Goal | None,
        budget: ResourceBudget,
    ) -> tuple[bool, str]:
        origin = goal.origin if goal is not None else None
        if origin is None and proposal.goal_origin:
            try:
                origin = GoalOrigin(proposal.goal_origin)
            except (TypeError, ValueError):
                return False, "invalid goal origin"
        if not _valid_cost(proposal.compute_cost) or not _valid_cost(
            proposal.financial_cost
        ):
            return False, "action costs must be finite and non-negative"
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
        if not _valid_cost(self.autonomy.max_compute_cost) or not _valid_cost(
            self.autonomy.max_financial_cost
        ):
            return False, "autonomy cost limits are invalid"
        if proposal.compute_cost > self.autonomy.max_compute_cost:
            return False, "compute cost exceeds policy"
        if proposal.financial_cost > self.autonomy.max_financial_cost:
            return False, "financial cost exceeds policy"
        if not _valid_cost(budget.remaining_compute) or not _valid_cost(
            budget.remaining_financial
        ):
            return False, "resource budget is invalid"
        if proposal.compute_cost > budget.remaining_compute:
            return False, "compute budget exhausted"
        if proposal.financial_cost > budget.remaining_financial:
            return False, "financial budget exhausted"
        if proposal.kind in self.autonomy.require_human_approval:
            allowed, reason = self._human_approval_allows(proposal)
            if not allowed:
                return False, reason
        return True, "ok"

    def _human_approval_allows(self, proposal: ActionProposal) -> tuple[bool, str]:
        store = self._approval_store
        if store is None:
            return False, "requires human approval"
        digest = compute_action_digest(proposal)
        explicit = (proposal.payload or {}).get("approval_id")
        if explicit:
            record = store.get(str(explicit))
            if record is None:
                return False, "requires human approval"
            if record.status == "EXPIRED":
                return False, "approval expired"
            if record.status == "DENIED":
                return False, "approval denied"
            if record.status == "CONSUMED":
                return False, "approval already consumed"
            if record.status != "APPROVED":
                return False, "requires human approval"
            if record.action_digest != digest:
                return False, "action digest mismatch"
            return True, "ok"
        record = store.find_approved(digest)
        if record is None:
            return False, "requires human approval"
        return True, "ok"


def _valid_cost(value: float) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and isfinite(value)
        and value >= 0.0
    )
