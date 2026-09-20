"""Phase-7 versioned capability registry, activation, and rollback."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from math import isfinite
from typing import Iterable

from ..experience.refs import stable_ref
from .candidate import EvolutionCandidate
from .experiment import (
    EvolutionExperiment,
    ExperimentEvaluation,
    ExperimentResult,
)


class CapabilityState(str, Enum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    DORMANT = "DORMANT"
    DEPRECATED = "DEPRECATED"
    ROLLED_BACK = "ROLLED_BACK"


@dataclass
class CapabilityVersion:
    capability_id: str
    version_id: str
    implementation_ref: str
    born_at: datetime
    candidate_id: str
    experiment_id: str
    evaluation_id: str
    parent_version_id: str | None
    rollback_target: str | None
    supporting_evidence_refs: list[str] = field(default_factory=list)
    counter_evidence_refs: list[str] = field(default_factory=list)
    applicability_context: list[str] = field(default_factory=list)
    confidence: float = 0.0
    usage_count: int = 0
    success_count: int = 0
    last_verified_at: datetime | None = None
    state: CapabilityState = CapabilityState.ACTIVE
    superseded_by: str | None = None
@dataclass(frozen=True)
class CapabilityActivation:
    activation_id: str
    capability_id: str
    from_version_id: str | None
    to_version_id: str
    candidate_id: str
    experiment_id: str
    evaluation_id: str
    activated_at: datetime
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class CapabilityRollback:
    rollback_id: str
    capability_id: str
    from_version_id: str
    restored_version_id: str
    rolled_back_at: datetime
    reason: str
    evidence_refs: tuple[str, ...]


class EvolutionCapabilityRegistry:
    def __init__(
        self,
        *,
        versions: Iterable[CapabilityVersion] = (),
        activations: Iterable[CapabilityActivation] = (),
        rollbacks: Iterable[CapabilityRollback] = (),
    ) -> None:
        self.versions: list[CapabilityVersion] = list(versions)
        self.activations: list[CapabilityActivation] = list(activations)
        self.rollbacks: list[CapabilityRollback] = list(rollbacks)

    def active(self, capability_id: str) -> CapabilityVersion:
        rows = [
            row
            for row in self.versions
            if row.capability_id == capability_id
            and row.state is CapabilityState.ACTIVE
        ]
        if len(rows) != 1:
            raise KeyError(capability_id)
        return rows[0]

    def register_baseline(
        self,
        *,
        capability_id: str,
        implementation_ref: str,
        registered_at: datetime,
        evidence_refs: Iterable[str] = (),
        applicability_context: Iterable[str] = (),
        confidence: float = 1.0,
    ) -> CapabilityVersion:
        capability_id = str(capability_id).strip()
        implementation_ref = str(implementation_ref).strip()
        if not capability_id or not implementation_ref:
            raise ValueError("baseline capability requires id and implementation_ref")
        if _protected(capability_id):
            raise PermissionError("evolution cannot register protected governance capability")
        score = float(confidence)
        if not isfinite(score) or not 0.0 <= score <= 1.0:
            raise ValueError("capability confidence must be within 0..1")
        try:
            active = self.active(capability_id)
        except KeyError:
            active = None
        if active is not None:
            if active.implementation_ref != implementation_ref:
                raise ValueError("active baseline capability disagrees with implementation_ref")
            return active
        refs = _refs(evidence_refs)
        item = CapabilityVersion(
            capability_id=capability_id,
            version_id=stable_ref(
                "evcap-baseline", capability_id, implementation_ref, list(refs)
            ),
            implementation_ref=implementation_ref,
            born_at=registered_at,
            candidate_id="BASELINE",
            experiment_id="BASELINE",
            evaluation_id="BASELINE",
            parent_version_id=None,
            rollback_target=None,
            supporting_evidence_refs=list(refs),
            applicability_context=list(_refs(applicability_context)),
            confidence=score,
            last_verified_at=registered_at,
        )
        self.versions.append(item)
        return item

    def promote(
        self,
        *,
        capability_id: str,
        candidate: EvolutionCandidate,
        experiment: EvolutionExperiment,
        evaluation: ExperimentEvaluation,
        activated_at: datetime,
        applicability_context: Iterable[str] = (),
        confidence: float = 0.8,
    ) -> CapabilityVersion:
        capability_id = str(capability_id).strip()
        if not capability_id:
            raise ValueError("capability_id is required")
        if _protected(capability_id):
            raise PermissionError("evolution cannot activate protected governance capability")
        if experiment.spec.candidate_id != candidate.candidate_id:
            raise ValueError("experiment does not belong to candidate")
        if evaluation.experiment_id != experiment.spec.experiment_id:
            raise ValueError("evaluation does not belong to experiment")
        if not any(
            item.evaluation_id == evaluation.evaluation_id
            for item in experiment.evaluations
        ):
            raise ValueError("evaluation is not recorded on experiment")
        if evaluation.result is not ExperimentResult.IMPROVEMENT_OBSERVED:
            raise PermissionError("promotion requires prospective improvement evidence")
        score = float(confidence)
        if not isfinite(score) or not 0.0 <= score <= 1.0:
            raise ValueError("capability confidence must be within 0..1")

        existing = next(
            (
                row
                for row in self.versions
                if row.candidate_id == candidate.candidate_id
                and row.evaluation_id == evaluation.evaluation_id
                and row.capability_id == capability_id
            ),
            None,
        )
        if existing is not None:
            return existing

        try:
            parent = self.active(capability_id)
        except KeyError:
            parent = None
        evidence_refs = _refs(
            [
                *candidate.evidence_refs,
                *evaluation.evidence_refs,
                *experiment.replay_evidence_refs,
                *experiment.shadow_evidence_refs,
            ]
        )
        version_id = stable_ref(
            "evcap",
            capability_id,
            candidate.candidate_id,
            experiment.spec.experiment_id,
            evaluation.evaluation_id,
            candidate.target,
        )
        item = CapabilityVersion(
            capability_id=capability_id,
            version_id=version_id,
            implementation_ref=candidate.target,
            born_at=activated_at,
            candidate_id=candidate.candidate_id,
            experiment_id=experiment.spec.experiment_id,
            evaluation_id=evaluation.evaluation_id,
            parent_version_id=None if parent is None else parent.version_id,
            rollback_target=None if parent is None else parent.version_id,
            supporting_evidence_refs=list(evidence_refs),
            applicability_context=list(_refs(applicability_context)),
            confidence=score,
            last_verified_at=evaluation.evaluated_at,
        )
        activation = CapabilityActivation(
            activation_id=stable_ref(
                "evactivate",
                capability_id,
                None if parent is None else parent.version_id,
                item.version_id,
                activated_at.isoformat(),
                evaluation.evaluation_id,
            ),
            capability_id=capability_id,
            from_version_id=None if parent is None else parent.version_id,
            to_version_id=item.version_id,
            candidate_id=candidate.candidate_id,
            experiment_id=experiment.spec.experiment_id,
            evaluation_id=evaluation.evaluation_id,
            activated_at=activated_at,
            evidence_refs=evidence_refs,
        )
        if parent is not None:
            parent.state = CapabilityState.SUPERSEDED
            parent.superseded_by = item.version_id
        self.versions.append(item)
        self.activations.append(activation)
        return item
    def rollback(
        self,
        capability_id: str,
        *,
        rolled_back_at: datetime,
        reason: str,
        evidence_refs: Iterable[str] = (),
    ) -> CapabilityVersion:
        current = self.active(capability_id)
        if current.rollback_target is None:
            raise ValueError("active capability has no rollback target")
        prior = next(
            (
                row
                for row in self.versions
                if row.version_id == current.rollback_target
                and row.capability_id == capability_id
            ),
            None,
        )
        if prior is None:
            raise ValueError("rollback target is missing")
        current.state = CapabilityState.ROLLED_BACK
        prior.state = CapabilityState.ACTIVE
        prior.superseded_by = None
        refs = _refs(evidence_refs)
        record = CapabilityRollback(
            rollback_id=stable_ref(
                "evrollback",
                capability_id,
                current.version_id,
                prior.version_id,
                rolled_back_at.isoformat(),
                reason,
                list(refs),
            ),
            capability_id=capability_id,
            from_version_id=current.version_id,
            restored_version_id=prior.version_id,
            rolled_back_at=rolled_back_at,
            reason=str(reason),
            evidence_refs=refs,
        )
        self.rollbacks.append(record)
        return prior

    def record_use(
        self,
        capability_id: str,
        *,
        success: bool,
        occurred_at: datetime,
    ) -> CapabilityVersion:
        item = self.active(capability_id)
        item.usage_count += 1
        if success:
            item.success_count += 1
            item.last_verified_at = occurred_at
        return item

    def set_state(
        self,
        version_id: str,
        state: CapabilityState | str,
    ) -> CapabilityVersion:
        item = next((row for row in self.versions if row.version_id == version_id), None)
        if item is None:
            raise KeyError(version_id)
        target = state if isinstance(state, CapabilityState) else CapabilityState(str(state))
        if target is CapabilityState.ACTIVE:
            raise PermissionError("activation must use promote or rollback")
        item.state = target
        return item
def _protected(value: str) -> bool:
    lowered = value.lower()
    return any(
        token in lowered
        for token in (
            "authorization",
            "autonomy",
            "constitution",
            "governance",
            "approval",
            "secret",
            "credential",
            "may_spawn",
        )
    )


def _refs(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values if str(value)))


__all__ = [
    "CapabilityActivation",
    "CapabilityRollback",
    "CapabilityState",
    "CapabilityVersion",
    "EvolutionCapabilityRegistry",
]
