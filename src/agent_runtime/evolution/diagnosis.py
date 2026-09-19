"""Phase-3 competing-cause diagnosis for Evolution Engine v1."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from math import isfinite
from typing import Iterable, Mapping

from ..experience.refs import stable_ref
from .evidence import EvolutionEvidence


class CausalRelation(str, Enum):
    CORRELATION = "CORRELATION"
    SUSPECTED_CAUSE = "SUSPECTED_CAUSE"
    SUPPORTED_CAUSE = "SUPPORTED_CAUSE"
    REFUTED = "REFUTED"
    UNKNOWN = "UNKNOWN"


class DiagnosisStatus(str, Enum):
    SUFFICIENT = "SUFFICIENT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


@dataclass(frozen=True)
class CauseHypothesis:
    cause_id: str
    statement: str
    relation: CausalRelation
    supporting_evidence_refs: tuple[str, ...]
    counter_evidence_refs: tuple[str, ...]
    confidence: float


@dataclass(frozen=True)
class EvolutionDiagnosis:
    diagnosis_id: str
    agent_id: str
    created_at: datetime
    experience_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    observed_pattern: str
    causes: tuple[CauseHypothesis, ...]
    unknowns: tuple[str, ...]
    discriminating_experiment: str
    applicability_scope: str
    status: DiagnosisStatus
    confidence: float


class EvolutionDiagnosisLedger:
    def __init__(self, items: Iterable[EvolutionDiagnosis] = ()) -> None:
        self.items: list[EvolutionDiagnosis] = list(items)

    def record(self, item: EvolutionDiagnosis) -> EvolutionDiagnosis:
        existing = next(
            (row for row in self.items if row.diagnosis_id == item.diagnosis_id),
            None,
        )
        if existing is not None:
            if existing != item:
                raise ValueError("evolution diagnosis id collision")
            return existing
        self.items.append(item)
        return item


def build_evolution_diagnosis(
    *,
    agent_id: str,
    created_at: datetime,
    experience_refs: Iterable[str],
    evidence: Mapping[str, EvolutionEvidence],
    evidence_refs: Iterable[str],
    observed_pattern: str,
    causes: Iterable[CauseHypothesis],
    unknowns: Iterable[str] = (),
    discriminating_experiment: str = "",
    applicability_scope: str = "",
    status: DiagnosisStatus | str = DiagnosisStatus.INSUFFICIENT_EVIDENCE,
    confidence: float = 0.0,
) -> EvolutionDiagnosis:
    agent_id = str(agent_id).strip()
    pattern = str(observed_pattern).strip()
    if not agent_id or not pattern:
        raise ValueError("diagnosis requires agent_id and observed_pattern")
    experiences = _refs(experience_refs)
    refs = _refs(evidence_refs)
    if not experiences:
        raise ValueError("diagnosis requires at least one experience")
    if not refs:
        raise ValueError("diagnosis requires evidence references")
    missing = [ref for ref in refs if ref not in evidence]
    if missing:
        raise ValueError("diagnosis references unknown evidence")
    if any(evidence[ref].agent_id != agent_id for ref in refs):
        raise ValueError("diagnosis evidence belongs to another agent")
    if any(evidence[ref].experience_id not in experiences for ref in refs):
        raise ValueError("diagnosis evidence is outside declared experiences")

    state = status if isinstance(status, DiagnosisStatus) else DiagnosisStatus(str(status))
    score = _score(confidence, "diagnosis confidence")
    cause_rows = tuple(causes)
    if not cause_rows:
        raise ValueError("diagnosis requires at least one candidate cause")
    if len({row.cause_id for row in cause_rows}) != len(cause_rows):
        raise ValueError("cause ids must be unique")

    for cause in cause_rows:
        if not str(cause.statement).strip():
            raise ValueError("cause statement is required")
        _score(cause.confidence, "cause confidence")
        cause_refs = set(cause.supporting_evidence_refs) | set(cause.counter_evidence_refs)
        if not cause_refs.issubset(set(refs)):
            raise ValueError("cause evidence must belong to diagnosis evidence")
        if cause.relation is CausalRelation.SUPPORTED_CAUSE:
            supporting = [
                evidence[ref] for ref in cause.supporting_evidence_refs
            ]
            if not supporting or not any(item.learning_eligible for item in supporting):
                raise ValueError(
                    "supported cause requires learning-eligible supporting evidence"
                )
            if cause.confidence < 0.5:
                raise ValueError("supported cause requires confidence >= 0.5")

    unknown_rows = _refs(unknowns)
    experiment = str(discriminating_experiment).strip()
    scope = str(applicability_scope).strip()
    if state is DiagnosisStatus.SUFFICIENT:
        if len(cause_rows) < 2:
            raise ValueError("sufficient diagnosis requires competing causes")
        if not experiment:
            raise ValueError(
                "sufficient diagnosis requires a discriminating experiment"
            )
        if not any(evidence[ref].learning_eligible for ref in refs):
            raise ValueError(
                "sufficient diagnosis requires learning-eligible evidence"
            )

    diagnosis_id = stable_ref(
        "evd",
        agent_id,
        list(experiences),
        list(refs),
        pattern,
        [
            (
                row.cause_id,
                row.statement,
                row.relation.value,
                list(row.supporting_evidence_refs),
                list(row.counter_evidence_refs),
                row.confidence,
            )
            for row in cause_rows
        ],
        state.value,
    )
    return EvolutionDiagnosis(
        diagnosis_id=diagnosis_id,
        agent_id=agent_id,
        created_at=created_at,
        experience_refs=experiences,
        evidence_refs=refs,
        observed_pattern=pattern,
        causes=cause_rows,
        unknowns=unknown_rows,
        discriminating_experiment=experiment,
        applicability_scope=scope,
        status=state,
        confidence=score,
    )


def make_cause(
    statement: str,
    *,
    relation: CausalRelation | str = CausalRelation.UNKNOWN,
    supporting_evidence_refs: Iterable[str] = (),
    counter_evidence_refs: Iterable[str] = (),
    confidence: float = 0.0,
) -> CauseHypothesis:
    text = str(statement).strip()
    if not text:
        raise ValueError("cause statement is required")
    rel = relation if isinstance(relation, CausalRelation) else CausalRelation(str(relation))
    support = _refs(supporting_evidence_refs)
    counter = _refs(counter_evidence_refs)
    score = _score(confidence, "cause confidence")
    return CauseHypothesis(
        cause_id=stable_ref("evc", text, rel.value, list(support), list(counter)),
        statement=text,
        relation=rel,
        supporting_evidence_refs=support,
        counter_evidence_refs=counter,
        confidence=score,
    )
def _score(value: float, label: str) -> float:
    score = float(value)
    if not isfinite(score) or not 0.0 <= score <= 1.0:
        raise ValueError(f"{label} must be within 0..1")
    return score


def _refs(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values if str(value)))


__all__ = [
    "CausalRelation",
    "CauseHypothesis",
    "DiagnosisStatus",
    "EvolutionDiagnosis",
    "EvolutionDiagnosisLedger",
    "build_evolution_diagnosis",
    "make_cause",
]
