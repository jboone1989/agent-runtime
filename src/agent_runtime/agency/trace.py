"""Machine-generated, non-authorizing traces of autonomous action selection."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..action.proposal import ActionProposal
from ..clock import new_id
from ..self_model.failure_adaptation import (
    PredictionFailureGuard,
    proposal_risk_domains,
    proposal_target_path,
)
from .selection import SelectionDecision
from .choice import PreChoiceBasis


class SelectionTraceStatus(str, Enum):
    ACTIVE = "ACTIVE"
    STALE = "STALE"


class SelectionTraceReason(str, Enum):
    BASELINE = "baseline"
    CONCERN = "concern"
    GUARD = "guard"
    CHOICE = "choice"


@dataclass(frozen=True)
class SelectionCandidate:
    action_id: str
    kind: str
    target_path: str
    risk_domains: list[str]
    authorized: bool
    deferred: bool = False
    coverage_skipped: bool = False


@dataclass
class SelectionTrace:
    trace_id: str
    cycle: int
    timestamp: datetime
    chosen_action_id: str
    chosen_action_kind: str
    considered_path: str
    candidates: list[SelectionCandidate]
    reason: SelectionTraceReason
    trigger_evidence_refs: list[str] = field(default_factory=list)
    status: SelectionTraceStatus = SelectionTraceStatus.ACTIVE
    stale_after_cycle: int = 0


class SelectionTraceTracker:
    """Records one primary INTERNAL choice per cycle and never changes policy."""

    def __init__(
        self,
        traces: list[SelectionTrace] | None = None,
        *,
        stale_cycles: int = 24,
        max_records: int = 256,
    ) -> None:
        self.traces = list(traces or [])
        self.stale_cycles = stale_cycles
        self.max_records = max_records

    def record(
        self,
        *,
        cycle: int,
        timestamp: datetime,
        proposals: list[ActionProposal],
        authorized: list[ActionProposal],
        selection_decisions: list[SelectionDecision],
        guards: list[PredictionFailureGuard],
        deferred: list[ActionProposal] | None = None,
        coverage_skipped: list[ActionProposal] | None = None,
        choice_bases: list[PreChoiceBasis] | None = None,
    ) -> SelectionTrace | None:
        if any(item.cycle == cycle for item in self.traces):
            return None
        chosen = next(
            (item for item in authorized if item.trigger == "INTERNAL"), None
        )
        if chosen is None:
            return None
        authorized_ids = {item.action_id for item in authorized}
        deferred_ids = {item.action_id for item in deferred or []}
        coverage_skipped_ids = {
            item.action_id for item in coverage_skipped or []
        }
        internal_candidates = [
            item for item in proposals if item.trigger == "INTERNAL"
        ]
        reason = SelectionTraceReason.BASELINE
        evidence = list(chosen.evidence)
        guard_id = str(
            chosen.payload.get("prediction_failure_guard_id") or ""
        )
        guard = next(
            (item for item in guards if item.guard_id == guard_id), None
        )
        decision = next(
            (
                item
                for item in reversed(selection_decisions)
                if item.cycle == cycle
                and chosen.action_id in item.selected_action_ids
            ),
            None,
        )
        choice_basis = next(
            (
                item
                for item in reversed(choice_bases or [])
                if item.cycle == cycle
                and chosen.action_id in item.selected_action_ids
            ),
            None,
        )
        if guard is not None:
            reason = SelectionTraceReason.GUARD
            evidence = [
                guard.guard_id,
                *guard.evidence_refs,
                *guard.diagnostic_prediction_ids,
            ]
        elif decision is not None:
            reason = SelectionTraceReason.CONCERN
            evidence = [decision.decision_id, *decision.evidence_refs]
        elif choice_basis is not None:
            reason = SelectionTraceReason.CHOICE
            evidence = [choice_basis.basis_id, *choice_basis.evidence_refs]
        trace = SelectionTrace(
            trace_id=new_id("seltr"),
            cycle=cycle,
            timestamp=timestamp,
            chosen_action_id=chosen.action_id,
            chosen_action_kind=chosen.kind,
            considered_path=proposal_target_path(chosen),
            candidates=[
                SelectionCandidate(
                    action_id=item.action_id,
                    kind=item.kind,
                    target_path=proposal_target_path(item),
                    risk_domains=sorted(proposal_risk_domains(item)),
                    authorized=item.action_id in authorized_ids,
                    deferred=item.action_id in deferred_ids,
                    coverage_skipped=item.action_id in coverage_skipped_ids,
                )
                for item in internal_candidates
            ],
            reason=reason,
            trigger_evidence_refs=list(dict.fromkeys(ref for ref in evidence if ref)),
            stale_after_cycle=cycle + self.stale_cycles,
        )
        self.traces.append(trace)
        if len(self.traces) > self.max_records:
            self.traces = self.traces[-self.max_records :]
        return trace

    def expire(self, *, cycle: int) -> list[SelectionTrace]:
        expired: list[SelectionTrace] = []
        for trace in self.traces:
            if (
                trace.status == SelectionTraceStatus.ACTIVE
                and cycle > trace.stale_after_cycle
            ):
                trace.status = SelectionTraceStatus.STALE
                expired.append(trace)
        return expired
