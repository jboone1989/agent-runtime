"""Persistent external missions with measurable progress and bounded experiments."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import math

from ..clock import new_id


class MissionDirection(str, Enum):
    AT_LEAST = "AT_LEAST"
    AT_MOST = "AT_MOST"


class MissionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class MissionExperimentStatus(str, Enum):
    PROPOSED = "PROPOSED"
    RUNNING = "RUNNING"
    SETTLED = "SETTLED"
    CANCELLED = "CANCELLED"


class MissionConclusion(str, Enum):
    SUPPORTED = "SUPPORTED"
    REFUTED = "REFUTED"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass
class MissionMetricObservation:
    observation_id: str
    mission_id: str
    observed_at: datetime
    value: float
    source: str
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class MissionExperiment:
    experiment_id: str
    mission_id: str
    strategy: str
    expected_effect: str
    hypothesis_id: str | None = None
    risk_notes: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    status: MissionExperimentStatus = MissionExperimentStatus.PROPOSED
    proposed_at: datetime | None = None
    started_at: datetime | None = None
    settled_at: datetime | None = None
    before_value: float | None = None
    after_value: float | None = None
    conclusion: MissionConclusion | None = None
    notes: str = ""


@dataclass
class ExternalMission:
    mission_id: str
    goal_id: str
    description: str
    metric: str
    baseline: float
    target: float
    direction: MissionDirection
    created_at: datetime
    deadline: datetime
    issued_by: str = "operator"
    status: MissionStatus = MissionStatus.ACTIVE
    constraints: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    hypothesis_refs: list[str] = field(default_factory=list)
    experiment_refs: list[str] = field(default_factory=list)
    completed_at: datetime | None = None


@dataclass(frozen=True)
class MissionProgress:
    mission_id: str
    status: MissionStatus
    current_value: float
    progress: float
    expected_progress: float
    pressure: float
    exploration_rate: float
    resource_willingness: float
    trajectory: str
    projected_value: float | None
    remaining_seconds: float


class MissionController:
    def __init__(
        self,
        *,
        missions: list[ExternalMission] | None = None,
        observations: list[MissionMetricObservation] | None = None,
        experiments: list[MissionExperiment] | None = None,
    ) -> None:
        self.missions = list(missions or [])
        self.observations = list(observations or [])
        self.experiments = list(experiments or [])

    def create(
        self,
        *,
        goal_id: str,
        description: str,
        metric: str,
        baseline: float,
        target: float,
        direction: MissionDirection,
        created_at: datetime,
        deadline: datetime,
        issued_by: str = "operator",
        constraints: list[str] | None = None,
        evidence_refs: list[str] | None = None,
    ) -> ExternalMission:
        _aware(created_at, "mission created_at")
        _aware(deadline, "mission deadline")
        if deadline <= created_at:
            raise ValueError("mission deadline must be after created_at")
        if not description.strip() or not metric.strip():
            raise ValueError("mission description and metric are required")
        baseline = _finite(baseline, "mission baseline")
        target = _finite(target, "mission target")
        if baseline == target:
            raise ValueError("mission target must differ from baseline")
        if direction is MissionDirection.AT_LEAST and target < baseline:
            raise ValueError("AT_LEAST mission target must exceed baseline")
        if direction is MissionDirection.AT_MOST and target > baseline:
            raise ValueError("AT_MOST mission target must be below baseline")
        mission = ExternalMission(
            mission_id=new_id("mission"),
            goal_id=goal_id,
            description=description.strip(),
            metric=metric.strip(),
            baseline=baseline,
            target=target,
            direction=direction,
            created_at=created_at,
            deadline=deadline,
            issued_by=issued_by.strip() or "operator",
            constraints=list(dict.fromkeys(constraints or [])),
            evidence_refs=list(dict.fromkeys(evidence_refs or [])),
        )
        self.missions.append(mission)
        self.observations.append(
            MissionMetricObservation(
                observation_id=new_id("mission-observation"),
                mission_id=mission.mission_id,
                observed_at=created_at,
                value=baseline,
                source="baseline",
                evidence_refs=list(mission.evidence_refs),
            )
        )
        return mission

    def get(self, mission_id: str) -> ExternalMission:
        for mission in self.missions:
            if mission.mission_id == mission_id:
                return mission
        raise KeyError(mission_id)

    def active(self) -> list[ExternalMission]:
        return [m for m in self.missions if m.status is MissionStatus.ACTIVE]

    def latest_observation(self, mission_id: str) -> MissionMetricObservation:
        rows = [
            (index, observation)
            for index, observation in enumerate(self.observations)
            if observation.mission_id == mission_id
        ]
        if not rows:
            raise KeyError(f"mission has no metric observation: {mission_id}")
        return max(rows, key=lambda item: (item[1].observed_at, item[0]))[1]

    def observe_metric(
        self,
        mission_id: str,
        *,
        value: float,
        observed_at: datetime,
        source: str,
        evidence_refs: list[str] | None = None,
    ) -> MissionMetricObservation:
        mission = self.get(mission_id)
        _aware(observed_at, "mission metric observed_at")
        if not source.strip():
            raise ValueError("mission metric source is required")
        if observed_at < mission.created_at:
            raise ValueError("mission metric predates mission")
        observation = MissionMetricObservation(
            observation_id=new_id("mission-observation"),
            mission_id=mission_id,
            observed_at=observed_at,
            value=_finite(value, "mission metric value"),
            source=source.strip(),
            evidence_refs=list(dict.fromkeys(evidence_refs or [])),
        )
        self.observations.append(observation)
        self.refresh(observed_at)
        return observation

    def progress(self, mission_id: str, now: datetime) -> MissionProgress:
        mission = self.get(mission_id)
        _aware(now, "mission progress time")
        current = self.latest_observation(mission_id).value
        span = abs(mission.target - mission.baseline)
        if mission.direction is MissionDirection.AT_LEAST:
            achieved = (current - mission.baseline) / span
        else:
            achieved = (mission.baseline - current) / span
        progress = min(1.0, max(0.0, achieved))
        total = max(1.0, (mission.deadline - mission.created_at).total_seconds())
        elapsed = min(total, max(0.0, (now - mission.created_at).total_seconds()))
        expected = elapsed / total
        pace_gap = max(0.0, expected - progress)
        pressure = 0.0
        if mission.status is MissionStatus.ACTIVE:
            pressure = min(1.0, 0.10 + 0.75 * pace_gap + 0.15 * expected**3)
        trajectory = "ON_TRACK"
        if len([o for o in self.observations if o.mission_id == mission_id]) <= 1:
            trajectory = "UNKNOWN"
        elif progress + 0.05 < expected:
            trajectory = "BEHIND"
        elif progress > expected + 0.05:
            trajectory = "AHEAD"
        projected = self._projected_value(mission, now)
        return MissionProgress(
            mission_id=mission_id,
            status=mission.status,
            current_value=current,
            progress=progress,
            expected_progress=expected,
            pressure=pressure,
            exploration_rate=min(0.9, 0.15 + 0.65 * pressure),
            resource_willingness=min(0.85, 0.20 + 0.55 * pressure),
            trajectory=trajectory,
            projected_value=projected,
            remaining_seconds=max(0.0, (mission.deadline - now).total_seconds()),
        )

    def refresh(self, now: datetime) -> None:
        _aware(now, "mission refresh time")
        for mission in self.missions:
            if mission.status is not MissionStatus.ACTIVE:
                continue
            eligible = [
                observation
                for observation in self.observations
                if observation.mission_id == mission.mission_id
                and observation.observed_at <= mission.deadline
            ]
            reached_by_deadline = any(
                observation.value >= mission.target
                if mission.direction is MissionDirection.AT_LEAST
                else observation.value <= mission.target
                for observation in eligible
            )
            if reached_by_deadline:
                mission.status = MissionStatus.SUCCEEDED
                mission.completed_at = min(
                    observation.observed_at
                    for observation in eligible
                    if (
                        observation.value >= mission.target
                        if mission.direction is MissionDirection.AT_LEAST
                        else observation.value <= mission.target
                    )
                )
            elif now >= mission.deadline:
                mission.status = MissionStatus.FAILED
                mission.completed_at = mission.deadline

    def propose_experiment(
        self,
        mission_id: str,
        *,
        strategy: str,
        expected_effect: str,
        proposed_at: datetime,
        hypothesis_id: str | None = None,
        risk_notes: list[str] | None = None,
        evidence_refs: list[str] | None = None,
    ) -> MissionExperiment:
        mission = self.get(mission_id)
        if mission.status is not MissionStatus.ACTIVE:
            raise ValueError("mission experiment requires an active mission")
        _aware(proposed_at, "mission experiment proposed_at")
        if not strategy.strip() or not expected_effect.strip():
            raise ValueError("mission experiment requires strategy and expected effect")
        experiment = MissionExperiment(
            experiment_id=new_id("mission-experiment"),
            mission_id=mission_id,
            strategy=strategy.strip(),
            expected_effect=expected_effect.strip(),
            hypothesis_id=hypothesis_id,
            risk_notes=list(dict.fromkeys(risk_notes or [])),
            evidence_refs=list(dict.fromkeys(evidence_refs or [])),
            proposed_at=proposed_at,
        )
        self.experiments.append(experiment)
        mission.experiment_refs.append(experiment.experiment_id)
        if hypothesis_id and hypothesis_id not in mission.hypothesis_refs:
            mission.hypothesis_refs.append(hypothesis_id)
        return experiment

    def start_experiment(self, experiment_id: str, *, started_at: datetime) -> MissionExperiment:
        experiment = self._experiment(experiment_id)
        mission = self.get(experiment.mission_id)
        if mission.status is not MissionStatus.ACTIVE:
            raise ValueError("cannot start experiment for inactive mission")
        if experiment.status is not MissionExperimentStatus.PROPOSED:
            raise ValueError("mission experiment is not proposed")
        if any(
            row.mission_id == mission.mission_id
            and row.status is MissionExperimentStatus.RUNNING
            for row in self.experiments
        ):
            raise ValueError("mission already has a running experiment")
        _aware(started_at, "mission experiment started_at")
        experiment.started_at = started_at
        experiment.before_value = self.latest_observation(mission.mission_id).value
        experiment.status = MissionExperimentStatus.RUNNING
        return experiment

    def settle_experiment(
        self,
        experiment_id: str,
        *,
        observed_at: datetime,
        result_value: float,
        conclusion: MissionConclusion,
        evidence_refs: list[str],
        notes: str = "",
    ) -> MissionExperiment:
        experiment = self._experiment(experiment_id)
        if experiment.status is not MissionExperimentStatus.RUNNING:
            raise ValueError("mission experiment is not running")
        if not evidence_refs:
            raise ValueError("settled mission experiment requires evidence")
        mission = self.get(experiment.mission_id)
        observation = self.observe_metric(
            mission.mission_id,
            value=result_value,
            observed_at=observed_at,
            source=f"experiment:{experiment.experiment_id}",
            evidence_refs=evidence_refs,
        )
        experiment.after_value = observation.value
        experiment.settled_at = observed_at
        experiment.conclusion = conclusion
        experiment.notes = notes.strip()
        experiment.evidence_refs = list(
            dict.fromkeys([*experiment.evidence_refs, *evidence_refs, observation.observation_id])
        )
        experiment.status = MissionExperimentStatus.SETTLED
        return experiment

    def cancel(self, mission_id: str, *, at: datetime) -> ExternalMission:
        mission = self.get(mission_id)
        _aware(at, "mission cancellation time")
        if mission.status is MissionStatus.ACTIVE:
            mission.status = MissionStatus.CANCELLED
            mission.completed_at = at
        return mission

    def _experiment(self, experiment_id: str) -> MissionExperiment:
        for experiment in self.experiments:
            if experiment.experiment_id == experiment_id:
                return experiment
        raise KeyError(experiment_id)

    def _projected_value(self, mission: ExternalMission, now: datetime) -> float | None:
        indexed = [
            (index, observation)
            for index, observation in enumerate(self.observations)
            if observation.mission_id == mission.mission_id
        ]
        rows = [
            observation
            for _, observation in sorted(
                indexed, key=lambda item: (item[1].observed_at, item[0])
            )
        ]
        if len(rows) < 2:
            return None
        first, last = rows[0], rows[-1]
        elapsed = (last.observed_at - first.observed_at).total_seconds()
        if elapsed <= 0:
            return None
        rate = (last.value - first.value) / elapsed
        remaining = max(0.0, (mission.deadline - max(now, last.observed_at)).total_seconds())
        return last.value + rate * remaining


def _finite(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
