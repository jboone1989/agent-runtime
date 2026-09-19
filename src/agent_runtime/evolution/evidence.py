"""Canonical Phase-2 evidence records for Evolution Engine v1."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from math import isfinite
from typing import Iterable

from ..experience.refs import stable_ref
from .experience import EvolutionExperience


class EvidenceClass(str, Enum):
    WORLD_OBSERVED = "WORLD_OBSERVED"
    SYSTEM_MEASURED = "SYSTEM_MEASURED"
    USER_PROVIDED = "USER_PROVIDED"
    MODEL_INFERRED = "MODEL_INFERRED"
    UNKNOWN = "UNKNOWN"


class EvidenceStatus(str, Enum):
    VERIFIED = "VERIFIED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    UNVERIFIABLE = "UNVERIFIABLE"


@dataclass(frozen=True, order=True)
class EvidenceMetric:
    name: str
    value: float
    unit: str = ""
@dataclass(frozen=True)
class EvolutionEvidence:
    evidence_id: str
    agent_id: str
    experience_id: str
    trace_id: str
    observed_at: datetime
    settled_at: datetime
    evidence_class: EvidenceClass
    status: EvidenceStatus
    outcome_id: str | None
    action_refs: tuple[str, ...]
    outcome_success: bool | None
    process_score: float | None
    quality_score: float | None
    compute_cost: float
    financial_cost: float
    metrics: tuple[EvidenceMetric, ...]
    side_effects: tuple[str, ...]
    confidence: float
    evidence_refs: tuple[str, ...]
    missing_evidence_units: tuple[str, ...]

    @property
    def learning_eligible(self) -> bool:
        return (
            self.status is EvidenceStatus.VERIFIED
            and self.evidence_class
            in {
                EvidenceClass.WORLD_OBSERVED,
                EvidenceClass.SYSTEM_MEASURED,
                EvidenceClass.USER_PROVIDED,
            }
            and self.confidence >= 0.5
            and not self.missing_evidence_units
        )
class EvolutionEvidenceLedger:
    def __init__(self, items: Iterable[EvolutionEvidence] = ()) -> None:
        self.items: list[EvolutionEvidence] = list(items)

    def record(self, item: EvolutionEvidence) -> EvolutionEvidence:
        existing = next(
            (row for row in self.items if row.evidence_id == item.evidence_id),
            None,
        )
        if existing is not None:
            if existing != item:
                raise ValueError("evolution evidence id collision")
            return existing
        self.items.append(item)
        return item

    def for_experience(self, experience_id: str) -> list[EvolutionEvidence]:
        return [
            item for item in self.items
            if item.experience_id == experience_id
        ]

    def eligible_for_experience(self, experience_id: str) -> list[EvolutionEvidence]:
        return [
            item for item in self.for_experience(experience_id)
            if item.learning_eligible
        ]


def build_evolution_evidence(
    experience: EvolutionExperience,
    *,
    settled_at: datetime,
    evidence_class: EvidenceClass | str,
    status: EvidenceStatus | str,
    outcome_id: str | None = None,
    action_refs: Iterable[str] | None = None,
    outcome_success: bool | None = None,
    process_score: float | None = None,
    quality_score: float | None = None,
    compute_cost: float | None = None,
    financial_cost: float | None = None,
    metrics: Iterable[EvidenceMetric] = (),
    side_effects: Iterable[str] = (),
    confidence: float = 1.0,
    evidence_refs: Iterable[str] = (),
    missing_evidence_units: Iterable[str] = (),
) -> EvolutionEvidence:
    source = (
        evidence_class
        if isinstance(evidence_class, EvidenceClass)
        else EvidenceClass(str(evidence_class))
    )
    state = (
        status
        if isinstance(status, EvidenceStatus)
        else EvidenceStatus(str(status))
    )
    if settled_at < experience.effective_at:
        raise ValueError("evidence cannot settle before the experience is effective")

    confidence = _score(confidence, "evidence confidence")
    process_score = _optional_score(process_score, "process_score")
    quality_score = _optional_score(quality_score, "quality_score")
    compute_cost = _non_negative(
        experience.compute_cost if compute_cost is None else compute_cost,
        "compute_cost",
    )
    financial_cost = _non_negative(
        experience.financial_cost if financial_cost is None else financial_cost,
        "financial_cost",
    )

    bound_actions = (
        experience.action_refs
        if action_refs is None
        else _refs(action_refs)
    )
    if any(ref not in experience.action_refs for ref in bound_actions):
        raise ValueError("evidence action does not belong to experience")
    if experience.action_refs and not bound_actions:
        raise ValueError("evidence must bind at least one experience action")

    metric_rows = tuple(sorted(_validate_metrics(metrics)))
    sides = _refs(side_effects)
    refs = _refs(evidence_refs)
    missing = _refs(missing_evidence_units)
    if state is EvidenceStatus.VERIFIED and source is EvidenceClass.UNKNOWN:
        raise ValueError("verified evidence requires a known evidence class")
    if state is EvidenceStatus.VERIFIED and missing:
        raise ValueError("verified evidence cannot have missing evidence units")

    evidence_id = stable_ref(
        "eve",
        experience.agent_id,
        experience.experience_id,
        settled_at.isoformat(),
        source.value,
        state.value,
        outcome_id or "",
        list(bound_actions),
        list(refs),
        [(item.name, item.value, item.unit) for item in metric_rows],
    )
    return EvolutionEvidence(
        evidence_id=evidence_id,
        agent_id=experience.agent_id,
        experience_id=experience.experience_id,
        trace_id=experience.trace_id,
        observed_at=experience.observed_at,
        settled_at=settled_at,
        evidence_class=source,
        status=state,
        outcome_id=outcome_id,
        action_refs=bound_actions,
        outcome_success=outcome_success,
        process_score=process_score,
        quality_score=quality_score,
        compute_cost=compute_cost,
        financial_cost=financial_cost,
        metrics=metric_rows,
        side_effects=sides,
        confidence=confidence,
        evidence_refs=refs,
        missing_evidence_units=missing,
    )
def _validate_metrics(values: Iterable[EvidenceMetric]) -> list[EvidenceMetric]:
    rows: list[EvidenceMetric] = []
    names: set[str] = set()
    for raw in values:
        name = str(raw.name).strip()
        unit = str(raw.unit).strip()
        value = float(raw.value)
        if not name:
            raise ValueError("evidence metric name is required")
        if name in names:
            raise ValueError("evidence metric names must be unique")
        if not isfinite(value):
            raise ValueError("evidence metric value must be finite")
        names.add(name)
        rows.append(EvidenceMetric(name=name, value=value, unit=unit))
    return rows


def _score(value: float, label: str) -> float:
    score = float(value)
    if not isfinite(score) or not 0.0 <= score <= 1.0:
        raise ValueError(f"{label} must be within 0..1")
    return score


def _optional_score(value: float | None, label: str) -> float | None:
    if value is None:
        return None
    return _score(value, label)


def _non_negative(value: float, label: str) -> float:
    number = float(value)
    if not isfinite(number) or number < 0.0:
        raise ValueError(f"{label} must be finite and non-negative")
    return number


def _refs(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values if str(value)))


__all__ = [
    "EvidenceClass",
    "EvidenceMetric",
    "EvidenceStatus",
    "EvolutionEvidence",
    "EvolutionEvidenceLedger",
    "build_evolution_evidence",
]
