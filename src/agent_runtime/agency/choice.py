"""Bounded execute/defer semantics for otherwise eligible INTERNAL actions."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..action.proposal import ActionProposal
from ..clock import new_id
from ..self_model.failure_adaptation import (
    proposal_risk_domains,
    proposal_target_path,
)


class ChoicePrecedence(str, Enum):
    EVIDENCE_FIRST = "evidence_first"
    COST_SYMMETRIC = "cost_symmetric"
    CONSERVATIVE_DEFAULT = "conservative_default"


class DeferralStatus(str, Enum):
    PENDING = "PENDING"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"


class ChoiceEvidenceKind(str, Enum):
    VIABILITY_THREAT = "VIABILITY_THREAT"
    PREDICTION_FAILURE = "PREDICTION_FAILURE"


@dataclass(frozen=True)
class ChoiceEvidence:
    evidence_id: str
    kind: ChoiceEvidenceKind
    target_path: str
    risk_domains: set[str]
    observed_value: object
    threshold_value: object
    evidence_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PreChoiceBasis:
    basis_id: str
    cycle: int
    timestamp: datetime
    target_path: str
    risk_domains: set[str]
    precedence: ChoicePrecedence
    candidate_action_ids: list[str]
    selected_action_ids: list[str]
    deferred_action_ids: list[str]
    evidence_refs: list[str] = field(default_factory=list)
    machine_evidence: ChoiceEvidence | None = None


@dataclass
class ActionDeferral:
    deferral_id: str
    basis_id: str
    cycle: int
    action_id: str
    action_kind: str
    resource: str
    target_path: str
    risk_domains: set[str]
    status: DeferralStatus = DeferralStatus.PENDING
    released_cycle: int | None = None
    released_action_id: str | None = None
    expires_cycle: int = 0
    evidence_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ChoiceResult:
    execution_proposals: list[ActionProposal]
    deferred_proposals: list[ActionProposal]
    basis: PreChoiceBasis | None


class BoundedChoiceEngine:
    """Changes execution, never authorization, for at most one cohort per cycle."""

    _EVIDENCE_KINDS = {"diagnose", "introspect", "observe", "probe_provider"}
    _LIFECYCLE_KEYS = {
        "agency_experiment_id",
        "agency_thread_id",
        "agency_discovery_id",
        "prediction_failure_guard_id",
        "selection_concern_id",
    }

    def __init__(
        self,
        bases: list[PreChoiceBasis] | None = None,
        deferrals: list[ActionDeferral] | None = None,
        *,
        deferral_expiry_cycles: int = 2,
        max_records: int = 256,
    ) -> None:
        self.bases = list(bases or [])
        self.deferrals = list(deferrals or [])
        self.deferral_expiry_cycles = deferral_expiry_cycles
        self.max_records = max_records

    def choose(
        self,
        proposals: list[ActionProposal],
        *,
        eligible_action_ids: set[str],
        cycle: int,
        timestamp: datetime,
        evidence_refs: list[str] | None = None,
        machine_evidence: list[ChoiceEvidence] | None = None,
    ) -> ChoiceResult:
        self.expire(cycle=cycle)
        internal = [
            item
            for item in proposals
            if item.trigger == "INTERNAL"
            and item.action_id in eligible_action_ids
            and not self._lifecycle_managed(item)
        ]
        cohort = self._first_cohort(internal)
        if len(cohort) < 2 or self._operator_overlap(proposals, cohort):
            return ChoiceResult(list(proposals), [], None)
        source = self._matching_evidence(cohort, machine_evidence or [])
        if source is None:
            return ChoiceResult(list(proposals), [], None)

        pending = [
            item
            for item in cohort
            if self._pending_for(item) is not None
        ]
        precedence = self._precedence(cohort)
        selected = pending or [self._select_baseline(cohort, precedence)]
        selected_ids = {item.action_id for item in selected}
        deferred = [item for item in cohort if item.action_id not in selected_ids]
        if not deferred:
            return ChoiceResult(list(proposals), [], None)

        refs = list(evidence_refs or [])
        refs.extend(source.evidence_refs)
        for item in cohort:
            refs.extend(item.evidence)
        basis = PreChoiceBasis(
            basis_id=new_id("pcb"),
            cycle=cycle,
            timestamp=timestamp,
            target_path=self._cohort_path(cohort),
            risk_domains=self._shared_domains(cohort),
            precedence=precedence,
            candidate_action_ids=[item.action_id for item in cohort],
            selected_action_ids=[item.action_id for item in selected],
            deferred_action_ids=[item.action_id for item in deferred],
            evidence_refs=list(dict.fromkeys(ref for ref in refs if ref)),
            machine_evidence=source,
        )
        self.bases.append(basis)
        for item in selected:
            pending_item = self._pending_for(item)
            if pending_item is not None:
                pending_item.status = DeferralStatus.RELEASED
                pending_item.released_cycle = cycle
                pending_item.released_action_id = item.action_id
        for item in deferred:
            self.deferrals.append(
                ActionDeferral(
                    deferral_id=new_id("adef"),
                    basis_id=basis.basis_id,
                    cycle=cycle,
                    action_id=item.action_id,
                    action_kind=item.kind,
                    resource=item.resource,
                    target_path=proposal_target_path(item),
                    risk_domains=proposal_risk_domains(item),
                    expires_cycle=cycle + self.deferral_expiry_cycles,
                    evidence_refs=[basis.basis_id, *basis.evidence_refs],
                )
            )
        self._trim()
        deferred_ids = {item.action_id for item in deferred}
        execution = [
            item for item in proposals if item.action_id not in deferred_ids
        ]
        return ChoiceResult(execution, deferred, basis)

    def expire(self, *, cycle: int) -> list[ActionDeferral]:
        expired: list[ActionDeferral] = []
        for item in self.deferrals:
            if (
                item.status == DeferralStatus.PENDING
                and cycle > item.expires_cycle
            ):
                item.status = DeferralStatus.EXPIRED
                expired.append(item)
        return expired

    def _first_cohort(
        self, proposals: list[ActionProposal]
    ) -> list[ActionProposal]:
        for anchor in proposals:
            cohort = [
                item for item in proposals if self._related(anchor, item)
            ]
            if len(cohort) >= 2:
                return cohort
        return []

    @staticmethod
    def _related(left: ActionProposal, right: ActionProposal) -> bool:
        if left is right:
            return True
        left_path = proposal_target_path(left)
        right_path = proposal_target_path(right)
        same_path = bool(left_path and left_path == right_path)
        same_domain = bool(
            proposal_risk_domains(left) & proposal_risk_domains(right)
        )
        return same_path or same_domain

    def _operator_overlap(
        self,
        proposals: list[ActionProposal],
        cohort: list[ActionProposal],
    ) -> bool:
        return any(
            item.trigger in {"USER", "REGULATION"}
            and any(self._related(item, candidate) for candidate in cohort)
            for item in proposals
        )

    def _pending_for(self, proposal: ActionProposal) -> ActionDeferral | None:
        target = proposal_target_path(proposal)
        return next(
            (
                item
                for item in reversed(self.deferrals)
                if item.status == DeferralStatus.PENDING
                and item.action_kind == proposal.kind
                and item.resource == proposal.resource
                and item.target_path == target
            ),
            None,
        )

    def _matching_evidence(
        self,
        cohort: list[ActionProposal],
        evidence: list[ChoiceEvidence],
    ) -> ChoiceEvidence | None:
        cohort_path = self._cohort_path(cohort)
        cohort_domains = self._shared_domains(cohort)
        return next(
            (
                item
                for item in evidence
                if (
                    bool(cohort_path and item.target_path == cohort_path)
                    or bool(item.risk_domains & cohort_domains)
                )
                and bool(item.evidence_refs)
            ),
            None,
        )

    def _precedence(
        self, cohort: list[ActionProposal]
    ) -> ChoicePrecedence:
        evidence = [item for item in cohort if item.kind in self._EVIDENCE_KINDS]
        if evidence and len(evidence) < len(cohort):
            return ChoicePrecedence.EVIDENCE_FIRST
        costs = [item.compute_cost + item.financial_cost for item in cohort]
        if max(costs) - min(costs) <= 0.02:
            return ChoicePrecedence.COST_SYMMETRIC
        return ChoicePrecedence.CONSERVATIVE_DEFAULT

    def _select_baseline(
        self,
        cohort: list[ActionProposal],
        precedence: ChoicePrecedence,
    ) -> ActionProposal:
        if precedence == ChoicePrecedence.EVIDENCE_FIRST:
            return next(
                item for item in cohort if item.kind in self._EVIDENCE_KINDS
            )
        if precedence == ChoicePrecedence.CONSERVATIVE_DEFAULT:
            return min(
                cohort,
                key=lambda item: (
                    item.compute_cost + item.financial_cost,
                    item.action_id,
                ),
            )
        return cohort[0]

    @staticmethod
    def _cohort_path(cohort: list[ActionProposal]) -> str:
        paths = [proposal_target_path(item) for item in cohort]
        return paths[0] if paths and all(path == paths[0] for path in paths) else ""

    @staticmethod
    def _shared_domains(cohort: list[ActionProposal]) -> set[str]:
        shared = proposal_risk_domains(cohort[0])
        for item in cohort[1:]:
            shared &= proposal_risk_domains(item)
        return shared

    def _lifecycle_managed(self, proposal: ActionProposal) -> bool:
        return any(proposal.payload.get(key) for key in self._LIFECYCLE_KEYS)

    def _trim(self) -> None:
        if len(self.bases) > self.max_records:
            self.bases = self.bases[-self.max_records :]
        if len(self.deferrals) > self.max_records:
            self.deferrals = self.deferrals[-self.max_records :]
