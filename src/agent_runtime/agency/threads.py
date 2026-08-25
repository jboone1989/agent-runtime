"""Persistent open questions that can re-enter action without a dialogue prompt."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..action.proposal import ActionProposal
from ..clock import new_id
from ..records import OutcomeRecord
from ..self_model.failure_adaptation import proposal_risk_domains


class AgencyThreadStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class AgencyThreadEventKind(str, Enum):
    OPENED = "OPENED"
    SCHEDULED_REENTRY = "SCHEDULED_REENTRY"
    DENIAL_OBSERVED = "DENIAL_OBSERVED"
    REVIEW_COMPLETED = "REVIEW_COMPLETED"
    PROGRESS = "PROGRESS"
    NO_CHANGE = "NO_CHANGE"
    CLOSED = "CLOSED"


@dataclass(frozen=True)
class AgencyThreadEvent:
    event_id: str
    thread_id: str
    kind: AgencyThreadEventKind
    cycle: int
    timestamp: datetime
    summary: str
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class AgencyThread:
    thread_id: str
    statement: str
    rationale: str
    status: AgencyThreadStatus
    created_cycle: int
    next_review_cycle: int
    review_interval_cycles: int
    trigger_action_kinds: set[str] = field(default_factory=set)
    trigger_risk_domains: set[str] = field(default_factory=set)
    evidence_refs: list[str] = field(default_factory=list)
    events: list[AgencyThreadEvent] = field(default_factory=list)
    auto_close_after_denial_followup: bool = False
    closed_cycle: int | None = None
    closure_reason: str | None = None
    closure_evidence_refs: list[str] = field(default_factory=list)
    last_discovery_review_cycle: int | None = None


class AgencyThreadTracker:
    def __init__(self, threads: list[AgencyThread] | None = None) -> None:
        self.threads = list(threads or [])

    def open_thread(
        self,
        *,
        thread_id: str,
        statement: str,
        rationale: str,
        cycle: int,
        timestamp: datetime,
        review_interval_cycles: int,
        trigger_action_kinds: set[str] | None = None,
        trigger_risk_domains: set[str] | None = None,
        evidence_refs: list[str] | None = None,
        auto_close_after_denial_followup: bool = False,
    ) -> AgencyThread:
        existing = self.thread(thread_id)
        if existing is not None:
            return existing
        if not thread_id.strip() or not statement.strip():
            raise ValueError("agency thread requires an id and statement")
        if review_interval_cycles < 1:
            raise ValueError("review interval must be positive")
        refs = list(evidence_refs or [])
        if not refs:
            raise ValueError("agency thread requires evidence")
        item = AgencyThread(
            thread_id=thread_id,
            statement=statement.strip(),
            rationale=rationale.strip(),
            status=AgencyThreadStatus.OPEN,
            created_cycle=cycle,
            next_review_cycle=cycle + review_interval_cycles,
            review_interval_cycles=review_interval_cycles,
            trigger_action_kinds=set(trigger_action_kinds or set()),
            trigger_risk_domains=set(trigger_risk_domains or set()),
            evidence_refs=refs,
            auto_close_after_denial_followup=auto_close_after_denial_followup,
        )
        item.events.append(
            AgencyThreadEvent(
                event_id=new_id("apte"),
                thread_id=thread_id,
                kind=AgencyThreadEventKind.OPENED,
                cycle=cycle,
                timestamp=timestamp,
                summary="evidence-linked agency thread opened",
                evidence_refs=refs,
            )
        )
        self.threads.append(item)
        return item

    def thread(self, thread_id: str) -> AgencyThread | None:
        return next((item for item in self.threads if item.thread_id == thread_id), None)

    def open_threads(self) -> list[AgencyThread]:
        return [item for item in self.threads if item.status == AgencyThreadStatus.OPEN]

    def scheduled_reentries(
        self, *, cycle: int, timestamp: datetime
    ) -> list[AgencyThreadEvent]:
        events: list[AgencyThreadEvent] = []
        for item in self.open_threads():
            if cycle < item.next_review_cycle:
                continue
            event = self._event(
                item,
                AgencyThreadEventKind.SCHEDULED_REENTRY,
                cycle=cycle,
                timestamp=timestamp,
                summary="open question re-entered without a dialogue prompt",
                evidence_refs=[item.thread_id, *item.evidence_refs],
            )
            item.next_review_cycle = cycle + item.review_interval_cycles
            events.append(event)
        return events

    def review_proposal(self, event: AgencyThreadEvent) -> ActionProposal:
        item = self.thread(event.thread_id)
        if item is None:
            raise ValueError(f"unknown agency thread {event.thread_id}")
        return ActionProposal(
            kind="introspect",
            resource="self",
            compute_cost=0.01,
            financial_cost=0.0,
            goal_id=None,
            reason=f"scheduled review of open agency thread {item.thread_id}",
            payload={"agency_thread_id": item.thread_id},
            trigger="INTERNAL",
            evidence=[item.thread_id, event.event_id, *item.evidence_refs],
            risk_domains=set(item.trigger_risk_domains),
        )

    def observe_denial(
        self,
        proposal: ActionProposal,
        denial: dict,
        *,
        cycle: int,
        timestamp: datetime,
    ) -> list[AgencyThreadEvent]:
        events: list[AgencyThreadEvent] = []
        domains = proposal_risk_domains(proposal)
        for item in self.open_threads():
            if not (
                proposal.kind in item.trigger_action_kinds
                or bool(domains & item.trigger_risk_domains)
            ):
                continue
            refs = [proposal.action_id, *proposal.evidence]
            for key in ("commitment_conflict", "prediction_failure_guard"):
                details = denial.get(key)
                if not isinstance(details, dict):
                    continue
                refs.extend(str(ref) for ref in details.get("evidence_refs", []))
            events.append(
                self._event(
                    item,
                    AgencyThreadEventKind.DENIAL_OBSERVED,
                    cycle=cycle,
                    timestamp=timestamp,
                    summary=f"relevant autonomous action denied: {proposal.kind}",
                    evidence_refs=list(dict.fromkeys(refs)),
                )
            )
        return events

    def observe_outcome(
        self,
        proposal: ActionProposal,
        outcome: OutcomeRecord,
        *,
        cycle: int,
        timestamp: datetime,
    ) -> AgencyThreadEvent | None:
        thread_id = str(proposal.payload.get("agency_thread_id") or "")
        item = self.thread(thread_id)
        if item is None or item.status != AgencyThreadStatus.OPEN or not outcome.success:
            return None
        event = self._event(
            item,
            AgencyThreadEventKind.REVIEW_COMPLETED,
            cycle=cycle,
            timestamp=timestamp,
            summary="scheduled prompt-free review action completed",
            evidence_refs=[proposal.action_id, outcome.outcome_id, *proposal.evidence],
        )
        if item.auto_close_after_denial_followup:
            denial = next(
                (
                    prior
                    for prior in reversed(item.events[:-1])
                    if prior.kind == AgencyThreadEventKind.DENIAL_OBSERVED
                    and prior.cycle < event.cycle
                ),
                None,
            )
            if denial is not None:
                self.close_thread(
                    item.thread_id,
                    cycle=cycle,
                    timestamp=timestamp,
                    reason="relevant denial was followed by an unprompted scheduled review",
                    evidence_refs=[
                        denial.event_id,
                        event.event_id,
                        outcome.outcome_id,
                        *proposal.evidence,
                    ],
                )
        return event

    def record_progress(
        self,
        thread_id: str,
        *,
        cycle: int,
        timestamp: datetime,
        summary: str,
        evidence_refs: list[str],
    ) -> AgencyThreadEvent:
        item = self.thread(thread_id)
        if item is None or item.status != AgencyThreadStatus.OPEN:
            raise ValueError(f"agency thread is not open: {thread_id}")
        if not evidence_refs:
            raise ValueError("agency thread progress requires evidence")
        return self._event(
            item,
            AgencyThreadEventKind.PROGRESS,
            cycle=cycle,
            timestamp=timestamp,
            summary=summary,
            evidence_refs=evidence_refs,
        )

    def record_no_change(
        self,
        thread_id: str,
        *,
        cycle: int,
        timestamp: datetime,
        reason: str,
        evidence_refs: list[str],
    ) -> AgencyThreadEvent:
        item = self.thread(thread_id)
        if item is None or item.status != AgencyThreadStatus.OPEN:
            raise ValueError(f"agency thread is not open: {thread_id}")
        return self._event(
            item,
            AgencyThreadEventKind.NO_CHANGE,
            cycle=cycle,
            timestamp=timestamp,
            summary=reason,
            evidence_refs=evidence_refs,
        )

    def close_thread(
        self,
        thread_id: str,
        *,
        cycle: int,
        timestamp: datetime,
        reason: str,
        evidence_refs: list[str],
    ) -> AgencyThreadEvent:
        item = self.thread(thread_id)
        if item is None or item.status != AgencyThreadStatus.OPEN:
            raise ValueError(f"agency thread is not open: {thread_id}")
        if not reason.strip() or not evidence_refs:
            raise ValueError("agency thread closure requires reason and evidence")
        event = self._event(
            item,
            AgencyThreadEventKind.CLOSED,
            cycle=cycle,
            timestamp=timestamp,
            summary=reason.strip(),
            evidence_refs=evidence_refs,
        )
        item.status = AgencyThreadStatus.CLOSED
        item.closed_cycle = cycle
        item.closure_reason = reason.strip()
        item.closure_evidence_refs = list(evidence_refs)
        return event

    @staticmethod
    def _event(
        item: AgencyThread,
        kind: AgencyThreadEventKind,
        *,
        cycle: int,
        timestamp: datetime,
        summary: str,
        evidence_refs: list[str],
    ) -> AgencyThreadEvent:
        event = AgencyThreadEvent(
            event_id=new_id("apte"),
            thread_id=item.thread_id,
            kind=kind,
            cycle=cycle,
            timestamp=timestamp,
            summary=summary,
            evidence_refs=list(dict.fromkeys(ref for ref in evidence_refs if ref)),
        )
        item.events.append(event)
        return event
