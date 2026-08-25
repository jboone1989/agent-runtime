"""Evidence coverage checks that prevent repeated empty INTERNAL diagnosis."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..action.evidence import classify_diagnostic_evidence
from ..action.proposal import ActionProposal
from ..clock import new_id
from ..records import ActionRecord, DecisionRecord, OutcomeRecord
from ..self_model.failure_adaptation import proposal_target_path


class CoverageResult(str, Enum):
    COVERED = "COVERED"
    UNCOVERED = "UNCOVERED"
    UNVERIFIABLE = "UNVERIFIABLE"


class CoverageProbeStatus(str, Enum):
    ACTIVE = "ACTIVE"
    RESOLVED = "RESOLVED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class CoverageInfluence:
    influence_id: str
    probe_id: str
    cycle: int
    target_path: str
    suppressed_action_id: str | None
    decision_id: str
    proposed_action_kinds: list[str]
    authorized_action_kinds: list[str]
    reason: str
    evidence_refs: list[str] = field(default_factory=list)
    suppressed_action_kind: str = "diagnose"
    authorization_result: str = "SUPPRESSED_BEFORE_EXECUTION"


@dataclass
class DiagnosticCoverageProbe:
    probe_id: str
    cycle: int
    timestamp: datetime
    target_path: str
    result: CoverageResult
    missing_units: list[str]
    source_action_id: str
    source_outcome_id: str
    evidence_refs: list[str] = field(default_factory=list)
    status: CoverageProbeStatus = CoverageProbeStatus.ACTIVE
    expires_cycle: int = 0
    resolution_outcome_id: str | None = None
    invalidated_cycle: int | None = None
    invalidation_evidence_refs: list[str] = field(default_factory=list)
    source_action_cycle: int | None = None
    source_outcome_cycle: int | None = None
    influences: list[CoverageInfluence] = field(default_factory=list)


@dataclass(frozen=True)
class CoverageDecision:
    execution_proposals: list[ActionProposal]
    skipped_proposals: list[ActionProposal]
    probes: list[DiagnosticCoverageProbe]


class DiagnosticCoverageEngine:
    _LIFECYCLE_KEYS = {
        "agency_experiment_id",
        "agency_thread_id",
        "agency_discovery_id",
        "prediction_failure_guard_id",
        "selection_concern_id",
    }

    def __init__(
        self,
        probes: list[DiagnosticCoverageProbe] | None = None,
        *,
        expiry_cycles: int = 24,
        max_records: int = 256,
        enforce_retry_boundary: bool = True,
    ) -> None:
        self.probes = list(probes or [])
        self.expiry_cycles = expiry_cycles
        self.max_records = max_records
        self.enforce_retry_boundary = enforce_retry_boundary

    def evaluate(
        self,
        proposals: list[ActionProposal],
        *,
        eligible_action_ids: set[str],
        actions: list[ActionRecord],
        outcomes: list[OutcomeRecord],
        active_risk_paths: dict[str, list[str]],
        cycle: int,
        timestamp: datetime,
    ) -> CoverageDecision:
        self.expire(cycle=cycle)
        execution = list(proposals)
        skipped: list[ActionProposal] = []
        created: list[DiagnosticCoverageProbe] = []
        outcome_by_action = {item.action_id: item for item in outcomes}
        for proposal in proposals:
            if (
                proposal.kind != "diagnose"
                or proposal.trigger != "INTERNAL"
                or proposal.action_id not in eligible_action_ids
                or any(
                    proposal.payload.get(key) for key in self._LIFECYCLE_KEYS
                )
            ):
                continue
            target_path = proposal_target_path(proposal)
            if not target_path:
                continue
            risk_refs = active_risk_paths.get(target_path, [])
            active = self.active_probe(target_path)
            if risk_refs:
                if active is not None:
                    active.status = CoverageProbeStatus.INVALIDATED
                    active.invalidated_cycle = cycle
                    active.invalidation_evidence_refs = list(risk_refs)
                continue
            if active is not None:
                if active.result in {
                    CoverageResult.COVERED,
                    CoverageResult.UNVERIFIABLE,
                }:
                    execution.remove(proposal)
                    skipped.append(proposal)
                continue
            if self._retry_deferred(proposal, cycle, target_path):
                execution.remove(proposal)
                skipped.append(proposal)
                continue
            history = [
                (action, outcome_by_action.get(action.action_id))
                for action in actions
                if action.kind == "diagnose"
                and action.trigger == "INTERNAL"
                and _action_target_path(action) == target_path
            ]
            source = next(
                (
                    (action, outcome)
                    for action, outcome in reversed(history)
                    if outcome is not None and outcome.success
                ),
                None,
            )
            if source is None:
                continue
            source_action, source_outcome = source
            if any(
                item.source_outcome_id == source_outcome.outcome_id
                for item in self.probes
            ):
                continue
            result, missing = _classify(source_outcome)
            refs = [
                source_action.action_id,
                source_outcome.outcome_id,
                *source_action.evidence,
                *list(source_outcome.effects.get("evidence_refs") or []),
            ]
            probe = DiagnosticCoverageProbe(
                probe_id=new_id("dcov"),
                cycle=cycle,
                timestamp=timestamp,
                target_path=target_path,
                result=result,
                missing_units=missing,
                source_action_id=source_action.action_id,
                source_outcome_id=source_outcome.outcome_id,
                evidence_refs=list(dict.fromkeys(ref for ref in refs if ref)),
                expires_cycle=cycle + self.expiry_cycles,
                source_action_cycle=source_action.cycle,
                source_outcome_cycle=source_outcome.cycle,
            )
            self.probes.append(probe)
            created.append(probe)
            if result in {
                CoverageResult.COVERED,
                CoverageResult.UNVERIFIABLE,
            }:
                execution.remove(proposal)
                skipped.append(proposal)
        self._trim()
        return CoverageDecision(execution, skipped, created)

    def observe_outcome(
        self,
        proposal: ActionProposal,
        outcome: OutcomeRecord,
        *,
        cycle: int,
    ) -> DiagnosticCoverageProbe | None:
        if proposal.kind != "diagnose" or proposal.trigger != "INTERNAL":
            return None
        target_path = proposal_target_path(proposal)
        probe = self.active_probe(target_path)
        if probe is None or probe.result != CoverageResult.UNCOVERED:
            return None
        result, missing = _classify(outcome)
        if result == CoverageResult.COVERED:
            probe.status = CoverageProbeStatus.RESOLVED
            probe.resolution_outcome_id = outcome.outcome_id
        else:
            probe.result = CoverageResult.UNVERIFIABLE
            probe.missing_units = missing
            probe.expires_cycle = cycle + self.expiry_cycles
            if outcome.outcome_id not in probe.evidence_refs:
                probe.evidence_refs.append(outcome.outcome_id)
        return probe

    def record_suppressions(
        self,
        skipped_proposals: list[ActionProposal],
        decision: DecisionRecord,
        *,
        cycle: int,
    ) -> list[CoverageInfluence]:
        created: list[CoverageInfluence] = []
        for proposal in skipped_proposals:
            target_path = proposal_target_path(proposal)
            probe = self.active_probe(target_path)
            authorization_result = "SUPPRESSED_BEFORE_EXECUTION"
            if probe is None:
                probe = self._latest_probe(target_path)
                if (
                    probe is None
                    or probe.status != CoverageProbeStatus.EXPIRED
                    or probe.resolution_outcome_id is not None
                ):
                    continue
                authorization_result = "DEFERRED_AFTER_EXPIRY_NO_NEW_EVIDENCE"
                reason = (
                    "diagnostic coverage retry boundary deferred internal "
                    "diagnosis without new machine evidence"
                )
            else:
                reason = decision.reason
            if any(
                item.suppressed_action_id == proposal.action_id
                for item in probe.influences
            ):
                continue
            influence = CoverageInfluence(
                influence_id=new_id("dcinf"),
                probe_id=probe.probe_id,
                cycle=cycle,
                target_path=target_path,
                suppressed_action_id=proposal.action_id,
                decision_id=decision.decision_id,
                proposed_action_kinds=list(decision.proposed_actions),
                authorized_action_kinds=list(decision.authorized_actions),
                reason=reason,
                evidence_refs=[
                    probe.probe_id,
                    probe.source_action_id,
                    probe.source_outcome_id,
                    proposal.action_id,
                    decision.decision_id,
                ],
                suppressed_action_kind=proposal.kind,
                authorization_result=authorization_result,
            )
            probe.influences.append(influence)
            created.append(influence)
        return created

    def backfill_decisions(
        self,
        decisions: list[DecisionRecord],
        *,
        actions: list[ActionRecord] | None = None,
        outcomes: list[OutcomeRecord] | None = None,
    ) -> list[CoverageInfluence]:
        """Link older probes to already-persisted structured suppression decisions."""
        created: list[CoverageInfluence] = []
        action_by_id = {item.action_id: item for item in actions or []}
        outcome_by_id = {item.outcome_id: item for item in outcomes or []}
        for probe in self.probes:
            source_action = action_by_id.get(probe.source_action_id)
            source_outcome = outcome_by_id.get(probe.source_outcome_id)
            if probe.source_action_cycle is None and source_action is not None:
                probe.source_action_cycle = source_action.cycle
            if probe.source_outcome_cycle is None and source_outcome is not None:
                probe.source_outcome_cycle = source_outcome.cycle
        linked_decisions = {
            item.decision_id
            for probe in self.probes
            for item in probe.influences
        }
        for probe in self.probes:
            for decision in decisions:
                if (
                    decision.decision_id in linked_decisions
                    or decision.cycle != probe.cycle
                    or decision.authorized_actions
                    or decision.reason
                    != "diagnostic coverage probe replaced redundant internal diagnosis"
                    or "diagnose" not in decision.proposed_actions
                ):
                    continue
                influence = CoverageInfluence(
                    influence_id=new_id("dcinf"),
                    probe_id=probe.probe_id,
                    cycle=decision.cycle,
                    target_path=probe.target_path,
                    suppressed_action_id=None,
                    decision_id=decision.decision_id,
                    proposed_action_kinds=list(decision.proposed_actions),
                    authorized_action_kinds=list(decision.authorized_actions),
                    reason=decision.reason,
                    evidence_refs=[
                        probe.probe_id,
                        probe.source_action_id,
                        probe.source_outcome_id,
                        decision.decision_id,
                    ],
                    suppressed_action_kind="diagnose",
                )
                probe.influences.append(influence)
                linked_decisions.add(decision.decision_id)
                created.append(influence)
        return created

    def active_probe(self, target_path: str) -> DiagnosticCoverageProbe | None:
        return next(
            (
                item
                for item in reversed(self.probes)
                if item.target_path == target_path
                and item.status == CoverageProbeStatus.ACTIVE
            ),
            None,
        )

    def _latest_probe(self, target_path: str) -> DiagnosticCoverageProbe | None:
        return next(
            (
                item
                for item in reversed(self.probes)
                if item.target_path == target_path
            ),
            None,
        )

    def _retry_deferred(
        self,
        proposal: ActionProposal,
        cycle: int,
        target_path: str,
    ) -> bool:
        """Bound the retry of an expired unresolved probe: a plain same-path
        diagnose without new machine evidence stays deferred for a bounded
        window after expiry."""
        if not self.enforce_retry_boundary:
            return False
        probe = self._latest_probe(target_path)
        if (
            probe is None
            or probe.status != CoverageProbeStatus.EXPIRED
            or probe.resolution_outcome_id is not None
            or probe.result
            not in {CoverageResult.UNVERIFIABLE, CoverageResult.UNCOVERED}
        ):
            return False
        if not (
            probe.expires_cycle < cycle <= probe.expires_cycle + self.expiry_cycles
        ):
            return False
        probe_refs = set(probe.evidence_refs)
        return not any(ref not in probe_refs for ref in proposal.evidence)

    def expire(self, *, cycle: int) -> list[DiagnosticCoverageProbe]:
        expired: list[DiagnosticCoverageProbe] = []
        for item in self.probes:
            if (
                item.status == CoverageProbeStatus.ACTIVE
                and cycle > item.expires_cycle
            ):
                item.status = CoverageProbeStatus.EXPIRED
                expired.append(item)
        return expired

    def _trim(self) -> None:
        if len(self.probes) > self.max_records:
            self.probes = self.probes[-self.max_records :]


def _action_target_path(action: ActionRecord) -> str:
    dimension = str(action.payload.get("dimension") or "")
    return f"internal_state.{dimension}" if dimension else ""


def _classify(outcome: OutcomeRecord) -> tuple[CoverageResult, list[str]]:
    result, missing = classify_diagnostic_evidence(outcome.effects)
    return CoverageResult(result.value), missing
