"""Roadmaps and deterministic serial/parallel scheduling for external missions."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
import math
from typing import Any


class MissionStepStatus(str, Enum):
    PLANNED = "PLANNED"
    READY = "READY"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


@dataclass
class MissionPlanStep:
    step_id: str
    mission_id: str
    title: str
    objective: str
    estimated_hours: float
    created_at: datetime
    depends_on_step_ids: list[str] = field(default_factory=list)
    resource_tags: list[str] = field(default_factory=list)
    exit_criteria: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None


@dataclass
class MissionRoadmap:
    roadmap_id: str
    mission_id: str
    version: int
    created_at: datetime
    revised_at: datetime
    steps: list[MissionPlanStep] = field(default_factory=list)
    rationale: str = ""
    evidence_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MissionScheduleItem:
    mission_id: str
    step_id: str
    title: str
    status: MissionStepStatus
    planned_start: datetime | None
    planned_finish: datetime | None
    lane: int | None
    blocked_by: tuple[str, ...]
    resource_tags: tuple[str, ...]


@dataclass(frozen=True)
class MissionPortfolioSchedule:
    as_of: datetime
    max_parallelism: int
    items: tuple[MissionScheduleItem, ...]
    unplanned_mission_ids: tuple[str, ...]


def validate_roadmap(roadmap: MissionRoadmap) -> None:
    _aware(roadmap.created_at, "roadmap created_at")
    _aware(roadmap.revised_at, "roadmap revised_at")
    if roadmap.version < 1:
        raise ValueError("roadmap version must be positive")
    ids = [step.step_id for step in roadmap.steps]
    if len(ids) != len(set(ids)):
        raise ValueError("roadmap step ids must be unique")
    id_set = set(ids)
    for step in roadmap.steps:
        _aware(step.created_at, "mission step created_at")
        if step.mission_id != roadmap.mission_id:
            raise ValueError("roadmap step belongs to another mission")
        if not step.title.strip() or not step.objective.strip():
            raise ValueError("mission step title and objective are required")
        if not math.isfinite(float(step.estimated_hours)) or step.estimated_hours <= 0:
            raise ValueError("mission step estimated_hours must be positive")
        if any(dep not in id_set for dep in step.depends_on_step_ids):
            raise ValueError("mission step dependency is outside its roadmap")
        if step.step_id in step.depends_on_step_ids:
            raise ValueError("mission step cannot depend on itself")
    _assert_acyclic(roadmap.steps)


def latest_roadmaps(
    roadmaps: list[MissionRoadmap],
) -> dict[str, MissionRoadmap]:
    result: dict[str, MissionRoadmap] = {}
    for roadmap in roadmaps:
        current = result.get(roadmap.mission_id)
        if current is None or (
            roadmap.version,
            roadmap.revised_at,
            roadmap.roadmap_id,
        ) > (
            current.version,
            current.revised_at,
            current.roadmap_id,
        ):
            result[roadmap.mission_id] = roadmap
    return result


def roadmap_complete(roadmap: MissionRoadmap) -> bool:
    return bool(roadmap.steps) and all(
        step.completed_at is not None or step.cancelled_at is not None
        for step in roadmap.steps
    )


def build_portfolio_schedule(
    *,
    missions: list[Any],
    roadmaps: list[MissionRoadmap],
    now: datetime,
    max_parallelism: int = 2,
) -> MissionPortfolioSchedule:
    _aware(now, "portfolio schedule time")
    max_parallelism = int(max_parallelism)
    if not 1 <= max_parallelism <= 32:
        raise ValueError("max_parallelism must be within 1..32")

    mission_by_id = {str(m.mission_id): m for m in missions}
    latest = latest_roadmaps(roadmaps)
    active = [
        mission
        for mission in missions
        if _enum_value(getattr(mission, "status", "")) == "ACTIVE"
    ]
    unplanned = tuple(
        str(m.mission_id)
        for m in active
        if str(m.mission_id) not in latest
        or not latest[str(m.mission_id)].steps
    )

    steps: dict[str, MissionPlanStep] = {}
    step_deadlines: dict[str, datetime] = {}
    for mission in active:
        roadmap = latest.get(str(mission.mission_id))
        if roadmap is None:
            continue
        validate_roadmap(roadmap)
        for step in roadmap.steps:
            if step.step_id in steps:
                raise ValueError("mission step ids must be globally unique")
            steps[step.step_id] = step
            step_deadlines[step.step_id] = mission.deadline

    graph: dict[str, set[str]] = {
        step_id: set(step.depends_on_step_ids)
        for step_id, step in steps.items()
    }
    impossible: dict[str, set[str]] = {step_id: set() for step_id in steps}
    for mission in active:
        mission_id = str(mission.mission_id)
        roadmap = latest.get(mission_id)
        if roadmap is None:
            continue
        roots = _root_steps(roadmap)
        for dependency_id in list(
            getattr(mission, "depends_on_mission_ids", ()) or ()
        ):
            dependency_id = str(dependency_id)
            dependency = mission_by_id.get(dependency_id)
            if dependency is None:
                for root in roots:
                    impossible[root.step_id].add(
                        "missing-mission:" + dependency_id
                    )
                continue
            dep_status = _enum_value(getattr(dependency, "status", ""))
            if dep_status == "SUCCEEDED":
                continue
            if dep_status in {"FAILED", "CANCELLED"}:
                for root in roots:
                    impossible[root.step_id].add(
                        "failed-mission:" + dependency_id
                    )
                continue
            dep_roadmap = latest.get(dependency_id)
            if dep_roadmap is None or not dep_roadmap.steps:
                for root in roots:
                    impossible[root.step_id].add(
                        "unplanned-mission:" + dependency_id
                    )
                continue
            if roadmap_complete(dep_roadmap):
                for root in roots:
                    impossible[root.step_id].add(
                        "await-mission-success:" + dependency_id
                    )
                continue
            for root in roots:
                graph[root.step_id].update(
                    step.step_id for step in _leaf_steps(dep_roadmap)
                )

    _assert_graph_acyclic(graph)
    order = _topological_order(graph, step_deadlines)
    lane_available = [now for _ in range(max_parallelism)]
    resource_intervals: dict[str, list[tuple[datetime, datetime]]] = {}
    finish_by_step: dict[str, datetime] = {}
    output: list[MissionScheduleItem] = []

    for step_id in order:
        step = steps[step_id]
        blocked = set(impossible[step_id])
        for dep in graph[step_id]:
            if dep not in finish_by_step:
                blocked.add(dep)
        if step.cancelled_at is not None:
            output.append(_item(step, MissionStepStatus.CANCELLED))
            continue
        if step.completed_at is not None:
            start = step.started_at or step.created_at
            finish_by_step[step_id] = step.completed_at
            output.append(
                _item(
                    step,
                    MissionStepStatus.COMPLETED,
                    start=start,
                    finish=step.completed_at,
                )
            )
            continue
        if blocked:
            output.append(
                _item(
                    step,
                    MissionStepStatus.BLOCKED,
                    blocked_by=tuple(sorted(blocked)),
                )
            )
            continue

        dep_finish = max(
            (finish_by_step[dep] for dep in graph[step_id]),
            default=now,
        )
        earliest = max(now, dep_finish)
        duration = timedelta(hours=float(step.estimated_hours))
        if step.started_at is not None:
            status = MissionStepStatus.RUNNING
            start = step.started_at
            finish = max(now + timedelta(seconds=1), start + duration)
            lane = min(range(max_parallelism), key=lambda value: lane_available[value])
            lane_available[lane] = max(lane_available[lane], finish)
            for tag in step.resource_tags:
                resource_intervals.setdefault(tag, []).append((max(now, start), finish))
            finish_by_step[step_id] = finish
        else:
            lane, start, finish = _choose_lane(
                earliest=earliest,
                duration=duration,
                lane_available=lane_available,
                resource_tags=tuple(step.resource_tags),
                resource_intervals=resource_intervals,
            )
            lane_available[lane] = finish
            for tag in step.resource_tags:
                resource_intervals.setdefault(tag, []).append((start, finish))
            finish_by_step[step_id] = finish
            actual_dependencies_done = all(
                steps[dep].completed_at is not None for dep in graph[step_id]
            )
            status = (
                MissionStepStatus.READY
                if actual_dependencies_done and start <= now
                else MissionStepStatus.PLANNED
            )
        output.append(
            _item(
                step,
                status,
                start=start,
                finish=finish,
                lane=lane,
            )
        )
    return MissionPortfolioSchedule(
        as_of=now,
        max_parallelism=max_parallelism,
        items=tuple(output),
        unplanned_mission_ids=unplanned,
    )


def _choose_lane(
    *,
    earliest: datetime,
    duration: timedelta,
    lane_available: list[datetime],
    resource_tags: tuple[str, ...],
    resource_intervals: dict[str, list[tuple[datetime, datetime]]],
) -> tuple[int, datetime, datetime]:
    candidates: list[tuple[datetime, datetime, int]] = []
    for lane, available in enumerate(lane_available):
        start = max(earliest, available)
        while True:
            finish = start + duration
            conflicts = [
                interval
                for tag in resource_tags
                for interval in resource_intervals.get(tag, ())
                if interval[0] < finish and start < interval[1]
            ]
            if not conflicts:
                break
            start = max(interval[1] for interval in conflicts)
        candidates.append((start + duration, start, lane))
    finish, start, lane = min(candidates)
    return lane, start, finish


def _item(
    step: MissionPlanStep,
    status: MissionStepStatus,
    *,
    start: datetime | None = None,
    finish: datetime | None = None,
    lane: int | None = None,
    blocked_by: tuple[str, ...] = (),
) -> MissionScheduleItem:
    return MissionScheduleItem(
        mission_id=step.mission_id,
        step_id=step.step_id,
        title=step.title,
        status=status,
        planned_start=start,
        planned_finish=finish,
        lane=lane,
        blocked_by=blocked_by,
        resource_tags=tuple(step.resource_tags),
    )


def _root_steps(roadmap: MissionRoadmap) -> list[MissionPlanStep]:
    return [
        step for step in roadmap.steps if not step.depends_on_step_ids
    ]
def _leaf_steps(roadmap: MissionRoadmap) -> list[MissionPlanStep]:
    depended_on = {
        dep
        for step in roadmap.steps
        for dep in step.depends_on_step_ids
    }
    return [
        step for step in roadmap.steps if step.step_id not in depended_on
    ]


def _topological_order(
    graph: dict[str, set[str]],
    deadlines: dict[str, datetime],
) -> list[str]:
    remaining = {node: set(deps) for node, deps in graph.items()}
    ordered: list[str] = []
    while remaining:
        ready = [
            node
            for node, deps in remaining.items()
            if not (deps & set(remaining))
        ]
        if not ready:
            raise ValueError("mission schedule contains a dependency cycle")
        ready.sort(key=lambda node: (deadlines[node], node))
        for node in ready:
            ordered.append(node)
            remaining.pop(node)
    return ordered
def _assert_acyclic(steps: list[MissionPlanStep]) -> None:
    graph = {
        step.step_id: set(step.depends_on_step_ids)
        for step in steps
    }
    _assert_graph_acyclic(graph)


def _assert_graph_acyclic(graph: dict[str, set[str]]) -> None:
    remaining = {node: set(deps) for node, deps in graph.items()}
    while remaining:
        ready = [
            node
            for node, deps in remaining.items()
            if not (deps & set(remaining))
        ]
        if not ready:
            raise ValueError("mission schedule contains a dependency cycle")
        for node in ready:
            remaining.pop(node)


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))
