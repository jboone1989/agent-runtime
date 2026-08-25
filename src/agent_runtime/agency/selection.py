"""Evidence-driven, non-timed asymmetry between otherwise eligible actions."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..action.proposal import ActionProposal
from ..clock import new_id
from ..records import ActionRecord, OutcomeRecord
from ..self_model.failure_adaptation import (
    PredictionFailureGuard,
    PredictionGuardState,
    proposal_risk_domains,
    proposal_target_path,
)
from ..social.commitment import Commitment, CommitmentState
from .discovery import AgencyDiscovery, AgencyDiscoveryStatus
from .threads import AgencyThread, AgencyThreadStatus


class SelectionConcernStatus(str, Enum):
    ACTIVE = "ACTIVE"
    RESOLVED = "RESOLVED"
    QUIET = "QUIET"
    EXPIRED = "EXPIRED"


@dataclass
class SelectionConcern:
    concern_id: str
    thread_id: str
    action_kind: str
    target_path: str
    risk_domains: set[str]
    status: SelectionConcernStatus
    created_cycle: int
    expires_cycle: int
    evidence_refs: list[str]
    influence_count: int = 0
    max_influences: int = 1
    diagnostic_outcome_id: str | None = None


@dataclass(frozen=True)
class SelectionDecision:
    decision_id: str
    concern_id: str
    thread_id: str
    cycle: int
    timestamp: datetime
    baseline_action_ids: list[str]
    selected_action_ids: list[str]
    deferred_action_ids: list[str]
    generated_evidence_action: bool
    reason: str
    evidence_refs: list[str] = field(default_factory=list)


class AgencySelectionEngine:
    def __init__(
        self,
        concerns: list[SelectionConcern] | None = None,
        decisions: list[SelectionDecision] | None = None,
        *,
        expiry_cycles: int = 6,
    ) -> None:
        self.concerns = list(concerns or [])
        self.decisions = list(decisions or [])
        self.expiry_cycles = expiry_cycles

    def observe_failures(
        self,
        *,
        actions: list[ActionRecord],
        outcomes: list[OutcomeRecord],
        threads: list[AgencyThread],
        guards: list[PredictionFailureGuard],
        commitments: list[Commitment],
        discoveries: list[AgencyDiscovery],
        cycle: int,
    ) -> list[SelectionConcern]:
        action_by_id = {item.action_id: item for item in actions}
        created: list[SelectionConcern] = []
        for outcome in outcomes:
            if outcome.success:
                continue
            action = action_by_id.get(outcome.action_id)
            if action is None or action.trigger != "INTERNAL":
                continue
            if any(outcome.outcome_id in item.evidence_refs for item in self.concerns):
                continue
            proposal = _proposal_from_record(action)
            target = proposal_target_path(proposal)
            domains = proposal_risk_domains(proposal)
            thread = _matching_thread(
                threads, action_kind=action.kind, risk_domains=domains
            )
            if thread is None:
                continue
            if _explained(
                proposal,
                target_path=target,
                domains=domains,
                guards=guards,
                commitments=commitments,
                discoveries=discoveries,
            ):
                continue
            concern = SelectionConcern(
                concern_id=new_id("apsel"),
                thread_id=thread.thread_id,
                action_kind=action.kind,
                target_path=target,
                risk_domains=domains & thread.trigger_risk_domains or domains,
                status=SelectionConcernStatus.ACTIVE,
                created_cycle=cycle,
                expires_cycle=cycle + self.expiry_cycles,
                evidence_refs=list(
                    dict.fromkeys(
                        [action.action_id, outcome.outcome_id, *action.evidence]
                    )
                ),
            )
            self.concerns.append(concern)
            created.append(concern)
        return created

    def select(
        self,
        proposals: list[ActionProposal],
        *,
        cycle: int,
        timestamp: datetime,
    ) -> list[ActionProposal]:
        selected = list(proposals)
        for concern in self.concerns:
            if concern.status != SelectionConcernStatus.ACTIVE:
                continue
            if cycle > concern.expires_cycle:
                concern.status = SelectionConcernStatus.EXPIRED
                continue
            if concern.influence_count >= concern.max_influences:
                concern.status = SelectionConcernStatus.QUIET
                continue
            retry = next(
                (
                    proposal
                    for proposal in selected
                    if proposal.trigger == "INTERNAL"
                    and proposal.kind == concern.action_kind
                    and proposal_target_path(proposal) == concern.target_path
                ),
                None,
            )
            if retry is None:
                continue
            evidence_action = next(
                (
                    proposal
                    for proposal in selected
                    if proposal.trigger == "INTERNAL"
                    and proposal.kind == "diagnose"
                    and proposal_target_path(proposal) == concern.target_path
                ),
                None,
            )
            generated = evidence_action is None
            if evidence_action is None:
                evidence_action = ActionProposal(
                    kind="diagnose",
                    resource="self",
                    compute_cost=min(retry.compute_cost, 0.03),
                    financial_cost=0.0,
                    goal_id=retry.goal_id,
                    reason=f"resolve selection concern {concern.concern_id}",
                    payload={
                        "dimension": _dimension(concern.target_path),
                        "selection_concern_id": concern.concern_id,
                    },
                    trigger="INTERNAL",
                    goal_origin=retry.goal_origin,
                    evidence=[concern.concern_id, *concern.evidence_refs],
                    risk_domains=set(concern.risk_domains),
                )
            else:
                evidence_action.payload["selection_concern_id"] = concern.concern_id
                for ref in [concern.concern_id, *concern.evidence_refs]:
                    if ref not in evidence_action.evidence:
                        evidence_action.evidence.append(ref)
            baseline_ids = [proposal.action_id for proposal in selected]
            retry_index = selected.index(retry)
            selected = [proposal for proposal in selected if proposal is not retry]
            if evidence_action not in selected:
                selected.insert(min(retry_index, len(selected)), evidence_action)
            elif selected.index(evidence_action) > retry_index:
                selected.remove(evidence_action)
                selected.insert(min(retry_index, len(selected)), evidence_action)
            concern.influence_count += 1
            decision = SelectionDecision(
                decision_id=new_id("apsd"),
                concern_id=concern.concern_id,
                thread_id=concern.thread_id,
                cycle=cycle,
                timestamp=timestamp,
                baseline_action_ids=baseline_ids,
                selected_action_ids=[proposal.action_id for proposal in selected],
                deferred_action_ids=[retry.action_id],
                generated_evidence_action=generated,
                reason="unexplained failure makes evidence acquisition precede retry",
                evidence_refs=[concern.concern_id, *concern.evidence_refs],
            )
            self.decisions.append(decision)
        return selected

    def observe_outcome(
        self, proposal: ActionProposal, outcome: OutcomeRecord
    ) -> SelectionConcern | None:
        concern_id = str(proposal.payload.get("selection_concern_id") or "")
        concern = self.concern(concern_id)
        if concern is None:
            return None
        if proposal.kind == "diagnose" and outcome.success:
            concern.status = SelectionConcernStatus.RESOLVED
            concern.diagnostic_outcome_id = outcome.outcome_id
        elif concern.influence_count >= concern.max_influences:
            concern.status = SelectionConcernStatus.QUIET
        return concern

    def concern(self, concern_id: str) -> SelectionConcern | None:
        return next(
            (item for item in self.concerns if item.concern_id == concern_id),
            None,
        )


def _matching_thread(
    threads: list[AgencyThread], *, action_kind: str, risk_domains: set[str]
) -> AgencyThread | None:
    return next(
        (
            item
            for item in threads
            if item.status == AgencyThreadStatus.OPEN
            and (
                action_kind in item.trigger_action_kinds
                or bool(risk_domains & item.trigger_risk_domains)
            )
        ),
        None,
    )


def _explained(
    proposal: ActionProposal,
    *,
    target_path: str,
    domains: set[str],
    guards: list[PredictionFailureGuard],
    commitments: list[Commitment],
    discoveries: list[AgencyDiscovery],
) -> bool:
    if any(
        guard.state in {PredictionGuardState.ACTIVE, PredictionGuardState.PROBATION}
        and (
            (guard.action_kind == proposal.kind and guard.target_path == target_path)
            or bool(guard.risk_domains & domains)
        )
        for guard in guards
    ):
        return True
    if any(
        item.status in {AgencyDiscoveryStatus.ACTIVE, AgencyDiscoveryStatus.PROBATION}
        and item.action_kind == proposal.kind
        and item.target_path == target_path
        for item in discoveries
    ):
        return True
    for commitment in commitments:
        if commitment.state not in {
            CommitmentState.ACTIVE,
            CommitmentState.VIOLATED,
            CommitmentState.RENEGOTIATING,
        }:
            continue
        if any(
            constraint.conflicts_with(
                kind=proposal.kind,
                resource=proposal.resource,
                trigger=proposal.trigger,
            )
            for constraint in commitment.constraints
        ):
            return True
    return False


def _proposal_from_record(action: ActionRecord) -> ActionProposal:
    return ActionProposal(
        kind=action.kind,
        resource=action.resource,
        compute_cost=action.compute_cost,
        financial_cost=action.financial_cost,
        goal_id=action.goal_id,
        reason=action.reason,
        payload=dict(action.payload),
        trigger=action.trigger,
        goal_origin=action.goal_origin,
        evidence=list(action.evidence),
        action_id=action.action_id,
    )


def _dimension(target_path: str) -> str:
    prefix = "internal_state."
    return target_path[len(prefix) :] if target_path.startswith(prefix) else target_path
