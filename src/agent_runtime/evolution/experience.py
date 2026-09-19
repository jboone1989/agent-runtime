"""Immutable Phase-1 experience projection for Evolution Engine v1."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Iterable

from ..experience.refs import stable_ref
from ..experience.trace import ExperienceTrace
from ..records import ActionRecord, OutcomeRecord


@dataclass(frozen=True, order=True)
class VersionBinding:
    """Version/provenance binding that was active for an experience."""

    kind: str
    name: str
    version: str


@dataclass(frozen=True)
class EvolutionExperience:
    """Sealed, replay-oriented projection of one Runtime experience trace."""

    experience_id: str
    trace_id: str
    agent_id: str
    logical_cycle: int
    observed_at: datetime
    effective_at: datetime
    sealed_at: datetime
    goal_refs: tuple[str, ...]
    plan_ref: str | None
    step_ref: str | None
    decision_ref: str | None
    action_refs: tuple[str, ...]
    outcome_refs: tuple[str, ...]
    caused_by: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    context_refs: tuple[str, ...]
    belief_refs: tuple[str, ...]
    version_bindings: tuple[VersionBinding, ...]
    compute_cost: float
    financial_cost: float
    outcome_known_at: datetime | None
    confidence: float


def seal_evolution_experience(
    trace: ExperienceTrace,
    *,
    actions: Iterable[ActionRecord] = (),
    outcomes: Iterable[OutcomeRecord] = (),
    sealed_at: datetime,
    context_refs: Iterable[str] = (),
    belief_refs: Iterable[str] = (),
    version_bindings: Iterable[VersionBinding] = (),
    outcome_known_at: datetime | None = None,
) -> EvolutionExperience:
    """Seal an immutable experience without inventing missing evidence."""

    action_rows = tuple(actions)
    outcome_rows = tuple(outcomes)
    action_by_id = {item.action_id: item for item in action_rows}
    outcome_by_id = {item.outcome_id: item for item in outcome_rows}

    missing_actions = [ref for ref in trace.action_refs if ref not in action_by_id]
    missing_outcomes = [ref for ref in trace.outcome_refs if ref not in outcome_by_id]
    if missing_actions:
        raise ValueError("experience seal is missing referenced actions")
    if missing_outcomes:
        raise ValueError("experience seal is missing referenced outcomes")
    if not isfinite(float(trace.confidence)) or not 0.0 <= float(trace.confidence) <= 1.0:
        raise ValueError("experience confidence must be within 0..1")

    compute_cost = sum(float(action_by_id[ref].compute_cost) for ref in trace.action_refs)
    financial_cost = sum(float(action_by_id[ref].financial_cost) for ref in trace.action_refs)
    if (
        not isfinite(compute_cost)
        or not isfinite(financial_cost)
        or compute_cost < 0.0
        or financial_cost < 0.0
    ):
        raise ValueError("experience costs must be finite and non-negative")

    if outcome_known_at is None and trace.outcome_refs:
        outcome_known_at = max(
            outcome_by_id[ref].timestamp for ref in trace.outcome_refs
        )
    if outcome_known_at is not None and outcome_known_at < trace.effective_at:
        raise ValueError("outcome_known_at cannot predate experience effective_at")
    if sealed_at < trace.effective_at:
        raise ValueError("sealed_at cannot predate experience effective_at")

    versions = tuple(
        sorted(
            {
                VersionBinding(
                    kind=str(item.kind).strip(),
                    name=str(item.name).strip(),
                    version=str(item.version).strip(),
                )
                for item in version_bindings
                if str(item.kind).strip()
                and str(item.name).strip()
                and str(item.version).strip()
            }
        )
    )
    contexts = _refs(context_refs)
    beliefs = _refs(belief_refs)
    goals = _refs(trace.goal_refs)
    actions_refs = _refs(trace.action_refs)
    outcomes_refs = _refs(trace.outcome_refs)
    caused_by = _refs(trace.caused_by)
    evidence_refs = _refs(trace.evidence_refs)

    experience_id = stable_ref(
        "evx",
        trace.agent_id,
        trace.trace_id,
        trace.logical_cycle,
        trace.effective_at.isoformat(),
        list(actions_refs),
        list(outcomes_refs),
        [(item.kind, item.name, item.version) for item in versions],
    )
    return EvolutionExperience(
        experience_id=experience_id,
        trace_id=trace.trace_id,
        agent_id=trace.agent_id,
        logical_cycle=int(trace.logical_cycle),
        observed_at=trace.observed_at,
        effective_at=trace.effective_at,
        sealed_at=sealed_at,
        goal_refs=goals,
        plan_ref=trace.plan_ref,
        step_ref=trace.step_ref,
        decision_ref=trace.decision_ref,
        action_refs=actions_refs,
        outcome_refs=outcomes_refs,
        caused_by=caused_by,
        evidence_refs=evidence_refs,
        context_refs=contexts,
        belief_refs=beliefs,
        version_bindings=versions,
        compute_cost=compute_cost,
        financial_cost=financial_cost,
        outcome_known_at=outcome_known_at,
        confidence=float(trace.confidence),
    )


def _refs(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values if str(value)))


__all__ = [
    "EvolutionExperience",
    "VersionBinding",
    "seal_evolution_experience",
]
