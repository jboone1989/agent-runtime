"""Phase-6 replay, shadow and prospective experiment engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from math import isfinite
from statistics import fmean
from typing import Iterable, Mapping

from ..experience.refs import stable_ref
from .candidate import EvolutionCandidate
from .evidence import EvolutionEvidence
from .experience import EvolutionExperience


class ExperimentArm(str, Enum):
    BASELINE = "BASELINE"
    CANDIDATE = "CANDIDATE"


class MetricDirection(str, Enum):
    HIGHER_BETTER = "HIGHER_BETTER"
    LOWER_BETTER = "LOWER_BETTER"
    NON_REGRESSION = "NON_REGRESSION"


class ExperimentStage(str, Enum):
    PROPOSED = "PROPOSED"
    REPLAY_VALIDATED = "REPLAY_VALIDATED"
    SHADOW = "SHADOW"
    PROSPECTIVE_TRIAL = "PROSPECTIVE_TRIAL"
    CLOSED = "CLOSED"


class ExperimentResult(str, Enum):
    IMPROVEMENT_OBSERVED = "IMPROVEMENT_OBSERVED"
    NO_IMPROVEMENT = "NO_IMPROVEMENT"
    REGRESSION_OBSERVED = "REGRESSION_OBSERVED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
@dataclass(frozen=True, order=True)
class ExperimentMetric:
    name: str
    direction: MetricDirection
    tolerance: float = 0.0
    primary: bool = False


@dataclass(frozen=True)
class FrozenExperimentSpec:
    experiment_id: str
    candidate_id: str
    baseline_ref: str
    frozen_at: datetime
    prospective_start_at: datetime
    prospective_end_at: datetime
    metrics: tuple[ExperimentMetric, ...]
    min_samples_per_arm: int
    regression_evidence_refs: tuple[str, ...]
    stop_conditions: tuple[str, ...]


@dataclass(frozen=True)
class ExperimentSample:
    sample_id: str
    experiment_id: str
    arm: ExperimentArm
    experience_id: str
    event_at: datetime
    known_at: datetime
    metric_values: tuple[tuple[str, float], ...]
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class ExperimentTransition:
    transition_id: str
    experiment_id: str
    from_stage: ExperimentStage
    to_stage: ExperimentStage
    occurred_at: datetime
    evidence_refs: tuple[str, ...]
@dataclass(frozen=True)
class ExperimentEvaluation:
    evaluation_id: str
    experiment_id: str
    evaluated_at: datetime
    result: ExperimentResult
    baseline_means: tuple[tuple[str, float], ...]
    candidate_means: tuple[tuple[str, float], ...]
    improved_metrics: tuple[str, ...]
    regressed_metrics: tuple[str, ...]
    baseline_samples: int
    candidate_samples: int
    evidence_refs: tuple[str, ...]


@dataclass
class EvolutionExperiment:
    spec: FrozenExperimentSpec
    stage: ExperimentStage = ExperimentStage.PROPOSED
    replay_evidence_refs: list[str] = field(default_factory=list)
    shadow_evidence_refs: list[str] = field(default_factory=list)
    samples: list[ExperimentSample] = field(default_factory=list)
    evaluations: list[ExperimentEvaluation] = field(default_factory=list)
    transitions: list[ExperimentTransition] = field(default_factory=list)


class EvolutionExperimentManager:
    def __init__(self, items: Iterable[EvolutionExperiment] = ()) -> None:
        self.items: list[EvolutionExperiment] = list(items)

    def get(self, experiment_id: str) -> EvolutionExperiment:
        item = next(
            (row for row in self.items if row.spec.experiment_id == experiment_id),
            None,
        )
        if item is None:
            raise KeyError(experiment_id)
        return item
    def create(
        self,
        candidate: EvolutionCandidate,
        *,
        frozen_at: datetime,
        prospective_start_at: datetime,
        prospective_end_at: datetime,
        metrics: Iterable[ExperimentMetric],
        min_samples_per_arm: int = 3,
        regression_evidence_refs: Iterable[str] = (),
        stop_conditions: Iterable[str] = (),
    ) -> EvolutionExperiment:
        if prospective_start_at < frozen_at:
            raise ValueError("prospective window must start after spec freeze")
        if prospective_end_at <= prospective_start_at:
            raise ValueError("prospective window end must follow start")
        if min_samples_per_arm < 1:
            raise ValueError("min_samples_per_arm must be positive")
        metric_rows = tuple(sorted(_validate_metrics(metrics)))
        regression_refs = _refs(regression_evidence_refs)
        stops = _refs(stop_conditions)
        experiment_id = stable_ref(
            "evexp",
            candidate.candidate_id,
            candidate.baseline_ref,
            frozen_at.isoformat(),
            prospective_start_at.isoformat(),
            prospective_end_at.isoformat(),
            [(m.name, m.direction.value, m.tolerance, m.primary) for m in metric_rows],
            min_samples_per_arm,
            list(regression_refs),
        )
        spec = FrozenExperimentSpec(
            experiment_id=experiment_id,
            candidate_id=candidate.candidate_id,
            baseline_ref=candidate.baseline_ref,
            frozen_at=frozen_at,
            prospective_start_at=prospective_start_at,
            prospective_end_at=prospective_end_at,
            metrics=metric_rows,
            min_samples_per_arm=int(min_samples_per_arm),
            regression_evidence_refs=regression_refs,
            stop_conditions=stops,
        )
        existing = next(
            (row for row in self.items if row.spec.experiment_id == experiment_id),
            None,
        )
        if existing is not None:
            return existing
        item = EvolutionExperiment(spec=spec)
        self.items.append(item)
        return item
    def mark_replay_validated(
        self,
        experiment_id: str,
        *,
        occurred_at: datetime,
        evidence_refs: Iterable[str],
    ) -> EvolutionExperiment:
        item = self.get(experiment_id)
        refs = _refs(evidence_refs)
        if not refs:
            raise ValueError("replay validation requires evidence")
        if item.stage is ExperimentStage.REPLAY_VALIDATED:
            return item
        if item.stage is not ExperimentStage.PROPOSED:
            raise ValueError("replay validation requires PROPOSED stage")
        item.replay_evidence_refs = list(refs)
        self._transition(item, ExperimentStage.REPLAY_VALIDATED, occurred_at, refs)
        return item

    def enter_shadow(
        self,
        experiment_id: str,
        *,
        occurred_at: datetime,
        evidence_refs: Iterable[str],
    ) -> EvolutionExperiment:
        item = self.get(experiment_id)
        refs = _refs(evidence_refs)
        if not refs:
            raise ValueError("shadow transition requires evidence")
        if item.stage is ExperimentStage.SHADOW:
            return item
        if item.stage is not ExperimentStage.REPLAY_VALIDATED:
            raise ValueError("shadow requires replay validation")
        item.shadow_evidence_refs = list(refs)
        self._transition(item, ExperimentStage.SHADOW, occurred_at, refs)
        return item

    def begin_prospective(
        self,
        experiment_id: str,
        *,
        occurred_at: datetime,
    ) -> EvolutionExperiment:
        item = self.get(experiment_id)
        if item.stage is ExperimentStage.PROSPECTIVE_TRIAL:
            return item
        if item.stage is not ExperimentStage.SHADOW:
            raise ValueError("prospective trial requires SHADOW stage")
        if occurred_at < item.spec.prospective_start_at:
            raise ValueError("prospective trial cannot start before frozen window")
        self._transition(item, ExperimentStage.PROSPECTIVE_TRIAL, occurred_at, ())
        return item
    def add_sample(
        self,
        experiment_id: str,
        *,
        arm: ExperimentArm | str,
        experience: EvolutionExperience,
        evidence: Mapping[str, EvolutionEvidence],
        evidence_refs: Iterable[str],
        known_at: datetime,
        metric_values: Mapping[str, float],
    ) -> ExperimentSample:
        item = self.get(experiment_id)
        if item.stage is not ExperimentStage.PROSPECTIVE_TRIAL:
            raise ValueError("samples require a prospective trial")
        arm_value = arm if isinstance(arm, ExperimentArm) else ExperimentArm(str(arm))
        if not (
            item.spec.prospective_start_at
            <= experience.effective_at
            < item.spec.prospective_end_at
        ):
            raise ValueError("experience is outside prospective window")
        if known_at <= experience.effective_at:
            raise ValueError("known_at must follow the decision/event time")
        refs = _refs(evidence_refs)
        if not refs:
            raise ValueError("experiment sample requires evidence")
        for ref in refs:
            row = evidence.get(ref)
            if row is None:
                raise ValueError("experiment sample references unknown evidence")
            if row.experience_id != experience.experience_id:
                raise ValueError("experiment evidence belongs to another experience")
            if row.settled_at > known_at:
                raise ValueError("experiment sample leaks evidence not known at known_at")
            if not row.learning_eligible:
                raise ValueError("prospective sample requires learning-eligible evidence")
        values = _metric_values(metric_values, item.spec.metrics)
        existing_arm = next(
            (
                row.arm
                for row in item.samples
                if row.experience_id == experience.experience_id
            ),
            None,
        )
        if existing_arm is not None and existing_arm is not arm_value:
            raise ValueError("one experience cannot belong to both experiment arms")
        sample_id = stable_ref(
            "evsample",
            experiment_id,
            arm_value.value,
            experience.experience_id,
            known_at.isoformat(),
            list(values),
            list(refs),
        )
        existing = next(
            (row for row in item.samples if row.sample_id == sample_id),
            None,
        )
        if existing is not None:
            return existing
        sample = ExperimentSample(
            sample_id=sample_id,
            experiment_id=experiment_id,
            arm=arm_value,
            experience_id=experience.experience_id,
            event_at=experience.effective_at,
            known_at=known_at,
            metric_values=values,
            evidence_refs=refs,
        )
        item.samples.append(sample)
        return sample
    def evaluate(
        self,
        experiment_id: str,
        *,
        evaluated_at: datetime,
    ) -> ExperimentEvaluation:
        item = self.get(experiment_id)
        if item.stage is not ExperimentStage.PROSPECTIVE_TRIAL:
            raise ValueError("evaluation requires a prospective trial")
        baseline = [row for row in item.samples if row.arm is ExperimentArm.BASELINE]
        candidate = [row for row in item.samples if row.arm is ExperimentArm.CANDIDATE]
        refs = _refs(
            ref
            for row in item.samples
            for ref in row.evidence_refs
        )
        if (
            len(baseline) < item.spec.min_samples_per_arm
            or len(candidate) < item.spec.min_samples_per_arm
        ):
            result = ExperimentResult.INSUFFICIENT_EVIDENCE
            base_means: tuple[tuple[str, float], ...] = ()
            cand_means: tuple[tuple[str, float], ...] = ()
            improved: tuple[str, ...] = ()
            regressed: tuple[str, ...] = ()
        else:
            base_means = _means(baseline, item.spec.metrics)
            cand_means = _means(candidate, item.spec.metrics)
            base_map = dict(base_means)
            cand_map = dict(cand_means)
            improved_names: list[str] = []
            regressed_names: list[str] = []
            primary_improved = False
            for metric in item.spec.metrics:
                left = base_map[metric.name]
                right = cand_map[metric.name]
                if metric.direction is MetricDirection.LOWER_BETTER:
                    delta = left - right
                else:
                    delta = right - left
                if metric.direction is MetricDirection.NON_REGRESSION:
                    if delta < -metric.tolerance:
                        regressed_names.append(metric.name)
                    continue
                if delta > metric.tolerance:
                    improved_names.append(metric.name)
                    if metric.primary:
                        primary_improved = True
                elif delta < -metric.tolerance:
                    regressed_names.append(metric.name)
            improved = tuple(improved_names)
            regressed = tuple(regressed_names)
            if regressed:
                result = ExperimentResult.REGRESSION_OBSERVED
            elif primary_improved:
                result = ExperimentResult.IMPROVEMENT_OBSERVED
            else:
                result = ExperimentResult.NO_IMPROVEMENT
        evaluation_id = stable_ref(
            "eveval",
            experiment_id,
            evaluated_at.isoformat(),
            result.value,
            list(base_means),
            list(cand_means),
            len(baseline),
            len(candidate),
            list(refs),
        )
        evaluation = ExperimentEvaluation(
            evaluation_id=evaluation_id,
            experiment_id=experiment_id,
            evaluated_at=evaluated_at,
            result=result,
            baseline_means=base_means,
            candidate_means=cand_means,
            improved_metrics=improved,
            regressed_metrics=regressed,
            baseline_samples=len(baseline),
            candidate_samples=len(candidate),
            evidence_refs=refs,
        )
        existing = next(
            (row for row in item.evaluations if row.evaluation_id == evaluation_id),
            None,
        )
        if existing is not None:
            return existing
        item.evaluations.append(evaluation)
        return evaluation

    def close(
        self,
        experiment_id: str,
        *,
        occurred_at: datetime,
        evidence_refs: Iterable[str] = (),
    ) -> EvolutionExperiment:
        item = self.get(experiment_id)
        if item.stage is ExperimentStage.CLOSED:
            return item
        if item.stage is not ExperimentStage.PROSPECTIVE_TRIAL:
            raise ValueError("only prospective trial can be closed")
        self._transition(item, ExperimentStage.CLOSED, occurred_at, _refs(evidence_refs))
        return item

    def abort(
        self,
        experiment_id: str,
        *,
        occurred_at: datetime,
        evidence_refs: Iterable[str],
    ) -> EvolutionExperiment:
        """Terminally stop an experiment from any pre-closed lifecycle stage.

        Abort is distinct from normal close: it is for invalidation/regression
        discovered before or during prospective execution. Requiring evidence
        prevents adapters from silently abandoning a durable experiment.
        """
        item = self.get(experiment_id)
        if item.stage is ExperimentStage.CLOSED:
            return item
        refs = _refs(evidence_refs)
        if not refs:
            raise ValueError("experiment abort requires evidence")
        self._transition(item, ExperimentStage.CLOSED, occurred_at, refs)
        return item

    @staticmethod
    def _transition(
        item: EvolutionExperiment,
        to_stage: ExperimentStage,
        occurred_at: datetime,
        evidence_refs: Iterable[str],
    ) -> None:
        refs = _refs(evidence_refs)
        prior = item.stage
        transition = ExperimentTransition(
            transition_id=stable_ref(
                "evtransition",
                item.spec.experiment_id,
                prior.value,
                to_stage.value,
                occurred_at.isoformat(),
                list(refs),
            ),
            experiment_id=item.spec.experiment_id,
            from_stage=prior,
            to_stage=to_stage,
            occurred_at=occurred_at,
            evidence_refs=refs,
        )
        item.transitions.append(transition)
        item.stage = to_stage


def _validate_metrics(values: Iterable[ExperimentMetric]) -> list[ExperimentMetric]:
    rows: list[ExperimentMetric] = []
    names: set[str] = set()
    for raw in values:
        name = str(raw.name).strip()
        if not name or name in names:
            raise ValueError("experiment metric names must be unique and non-empty")
        tolerance = float(raw.tolerance)
        if not isfinite(tolerance) or tolerance < 0:
            raise ValueError("metric tolerance must be finite and non-negative")
        direction = (
            raw.direction
            if isinstance(raw.direction, MetricDirection)
            else MetricDirection(str(raw.direction))
        )
        rows.append(
            ExperimentMetric(
                name=name,
                direction=direction,
                tolerance=tolerance,
                primary=bool(raw.primary),
            )
        )
        names.add(name)
    if not rows:
        raise ValueError("experiment requires metrics")
    if not any(row.primary for row in rows):
        raise ValueError("experiment requires at least one primary metric")
    return rows
def _metric_values(
    values: Mapping[str, float],
    contract: tuple[ExperimentMetric, ...],
) -> tuple[tuple[str, float], ...]:
    expected = {row.name for row in contract}
    supplied = {str(key) for key in values}
    if supplied != expected:
        raise ValueError("sample metric values must exactly match frozen metric contract")
    rows: list[tuple[str, float]] = []
    for metric in contract:
        value = float(values[metric.name])
        if not isfinite(value):
            raise ValueError("sample metrics must be finite")
        rows.append((metric.name, value))
    return tuple(rows)


def _means(
    samples: list[ExperimentSample],
    metrics: tuple[ExperimentMetric, ...],
) -> tuple[tuple[str, float], ...]:
    rows = []
    for metric in metrics:
        values = [dict(sample.metric_values)[metric.name] for sample in samples]
        rows.append((metric.name, fmean(values)))
    return tuple(rows)


def _refs(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values if str(value)))


__all__ = [
    "EvolutionExperiment",
    "EvolutionExperimentManager",
    "ExperimentArm",
    "ExperimentEvaluation",
    "ExperimentMetric",
    "ExperimentResult",
    "ExperimentSample",
    "ExperimentStage",
    "ExperimentTransition",
    "FrozenExperimentSpec",
    "MetricDirection",
]
