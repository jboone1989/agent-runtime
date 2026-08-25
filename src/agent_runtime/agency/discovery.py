"""Deterministic discoveries from evidence deltas between agency-thread reviews."""
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
from .threads import AgencyThread


class AgencyDiscoveryStatus(str, Enum):
    ACTIVE = "ACTIVE"
    PROBATION = "PROBATION"
    RESOLVED = "RESOLVED"
    WITHDRAWN = "WITHDRAWN"
    VOID = "VOID"


@dataclass
class AgencyDiscovery:
    discovery_id: str
    thread_id: str
    action_kind: str
    target_path: str
    risk_domains: set[str]
    status: AgencyDiscoveryStatus
    created_cycle: int
    anomaly_evidence_refs: list[str]
    required_action: str = "diagnose"
    diagnostic_outcome_id: str | None = None
    resolution_outcome_id: str | None = None
    withdrawal_reason: str | None = None
    falsifying_evidence_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DiscoveryReview:
    thread_id: str
    result: str
    reason: str
    evidence_refs: list[str]
    discovery_id: str | None = None


class AgencyDiscoveryEngine:
    def __init__(self, discoveries: list[AgencyDiscovery] | None = None) -> None:
        self.discoveries = list(discoveries or [])

    def review(
        self,
        thread: AgencyThread,
        *,
        actions: list[ActionRecord],
        outcomes: list[OutcomeRecord],
        guards: list[PredictionFailureGuard],
        commitments: list[Commitment],
        cycle: int,
    ) -> DiscoveryReview:
        start = (
            thread.created_cycle
            if thread.last_discovery_review_cycle is None
            else thread.last_discovery_review_cycle
        )
        action_by_id = {
            item.action_id: item
            for item in actions
            if start < item.cycle <= cycle and item.trigger == "INTERNAL"
        }
        failures = [
            item
            for item in outcomes
            if start < item.cycle <= cycle
            and not item.success
            and item.action_id in action_by_id
        ]
        thread.last_discovery_review_cycle = cycle
        for outcome in reversed(failures):
            action = action_by_id[outcome.action_id]
            proposal = _proposal_from_record(action)
            domains = proposal_risk_domains(proposal)
            if not (
                action.kind in thread.trigger_action_kinds
                or bool(domains & thread.trigger_risk_domains)
            ):
                continue
            target_path = proposal_target_path(proposal)
            evidence = [action.action_id, outcome.outcome_id, *action.evidence]
            if self._already_explained(
                proposal,
                target_path=target_path,
                domains=domains,
                guards=guards,
                commitments=commitments,
            ):
                return DiscoveryReview(
                    thread_id=thread.thread_id,
                    result="NO_CHANGE",
                    reason="anomaly already explained by an active restriction",
                    evidence_refs=evidence,
                )
            existing = self._matching_active(action.kind, target_path)
            if existing is not None:
                return DiscoveryReview(
                    thread_id=thread.thread_id,
                    result="NO_CHANGE",
                    reason="equivalent agency discovery already active",
                    evidence_refs=[existing.discovery_id, *evidence],
                )
            discovery = AgencyDiscovery(
                discovery_id=new_id("apd"),
                thread_id=thread.thread_id,
                action_kind=action.kind,
                target_path=target_path,
                risk_domains=domains & thread.trigger_risk_domains or domains,
                status=AgencyDiscoveryStatus.ACTIVE,
                created_cycle=cycle,
                anomaly_evidence_refs=list(dict.fromkeys(evidence)),
            )
            self.discoveries.append(discovery)
            return DiscoveryReview(
                thread_id=thread.thread_id,
                result="DISCOVERY",
                reason="unexplained action failure requires diagnosis before retry",
                evidence_refs=[discovery.discovery_id, *discovery.anomaly_evidence_refs],
                discovery_id=discovery.discovery_id,
            )
        return DiscoveryReview(
            thread_id=thread.thread_id,
            result="NO_CHANGE",
            reason="no new unexplained anomaly in the review window",
            evidence_refs=[thread.thread_id, f"cycles:{start + 1}-{cycle}"],
        )

    def conflict(self, proposal: ActionProposal) -> AgencyDiscovery | None:
        if proposal.trigger != "INTERNAL":
            return None
        target = proposal_target_path(proposal)
        for item in reversed(self.discoveries):
            if item.status != AgencyDiscoveryStatus.ACTIVE:
                continue
            if item.action_kind == proposal.kind and item.target_path == target:
                return item
        return None

    def remediation_proposals(self) -> list[ActionProposal]:
        return [
            ActionProposal(
                kind="diagnose",
                resource="self",
                compute_cost=0.01,
                financial_cost=0.0,
                goal_id=None,
                reason=f"validate agency discovery {item.discovery_id}",
                payload={
                    "agency_discovery_id": item.discovery_id,
                    "dimension": _dimension(item.target_path),
                },
                trigger="INTERNAL",
                evidence=[item.discovery_id, *item.anomaly_evidence_refs],
                risk_domains=set(item.risk_domains),
            )
            for item in self.discoveries
            if item.status == AgencyDiscoveryStatus.ACTIVE
        ]

    def observe_outcome(
        self, proposal: ActionProposal, outcome: OutcomeRecord
    ) -> AgencyDiscovery | None:
        discovery_id = str(proposal.payload.get("agency_discovery_id") or "")
        if discovery_id:
            item = self.discovery(discovery_id)
            if (
                item is not None
                and item.status == AgencyDiscoveryStatus.ACTIVE
                and proposal.kind == item.required_action
                and outcome.success
            ):
                item.status = AgencyDiscoveryStatus.PROBATION
                item.diagnostic_outcome_id = outcome.outcome_id
                return item
        target = proposal_target_path(proposal)
        item = self._matching(action_kind=proposal.kind, target_path=target)
        if item is None or item.status != AgencyDiscoveryStatus.PROBATION:
            return None
        if outcome.success:
            item.status = AgencyDiscoveryStatus.RESOLVED
            item.resolution_outcome_id = outcome.outcome_id
        else:
            item.status = AgencyDiscoveryStatus.ACTIVE
        return item

    def withdraw(
        self,
        discovery_id: str,
        *,
        reason: str,
        falsifying_evidence_refs: list[str],
    ) -> AgencyDiscovery:
        item = self.discovery(discovery_id)
        if item is None:
            raise ValueError(f"unknown agency discovery {discovery_id}")
        if not reason.strip() or not falsifying_evidence_refs:
            raise ValueError("withdrawal requires reason and falsifying evidence")
        item.status = AgencyDiscoveryStatus.WITHDRAWN
        item.withdrawal_reason = reason.strip()
        item.falsifying_evidence_refs = list(falsifying_evidence_refs)
        return item

    def discovery(self, discovery_id: str) -> AgencyDiscovery | None:
        return next(
            (item for item in self.discoveries if item.discovery_id == discovery_id),
            None,
        )

    def _matching_active(
        self, action_kind: str, target_path: str
    ) -> AgencyDiscovery | None:
        item = self._matching(action_kind=action_kind, target_path=target_path)
        return (
            item
            if item is not None
            and item.status in {
                AgencyDiscoveryStatus.ACTIVE,
                AgencyDiscoveryStatus.PROBATION,
            }
            else None
        )

    def _matching(
        self, *, action_kind: str, target_path: str
    ) -> AgencyDiscovery | None:
        return next(
            (
                item
                for item in reversed(self.discoveries)
                if item.action_kind == action_kind and item.target_path == target_path
            ),
            None,
        )

    @staticmethod
    def _already_explained(
        proposal: ActionProposal,
        *,
        target_path: str,
        domains: set[str],
        guards: list[PredictionFailureGuard],
        commitments: list[Commitment],
    ) -> bool:
        for guard in guards:
            if guard.state not in {
                PredictionGuardState.ACTIVE,
                PredictionGuardState.PROBATION,
            }:
                continue
            if (
                guard.action_kind == proposal.kind
                and guard.target_path == target_path
            ) or bool(guard.risk_domains & domains):
                return True
        for commitment in commitments:
            if commitment.state not in {
                CommitmentState.ACTIVE,
                CommitmentState.VIOLATED,
                CommitmentState.RENEGOTIATING,
            }:
                continue
            for constraint in commitment.constraints:
                if constraint.conflicts_with(
                    kind=proposal.kind,
                    resource=proposal.resource,
                    trigger=proposal.trigger,
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
