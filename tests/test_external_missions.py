from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json

import pytest

from agent_runtime.backends import FixtureAgentBackend
from agent_runtime.clock import FrozenClock
from agent_runtime.goals import (
    MissionConclusion,
    MissionDirection,
    MissionExperimentStatus,
    MissionStatus,
    MissionStepStatus,
)
from agent_runtime.goals.goal import GoalStatus
from agent_runtime.observations import Observation
from agent_runtime.runtime import AgentRuntime


NOW = datetime(2026, 9, 19, 0, 0, tzinfo=timezone.utc)


def _runtime(tmp_path, *, backend=None):
    clock = FrozenClock(NOW)
    rt = AgentRuntime.bootstrap(tmp_path, clock=clock, agent_backend=backend)
    return rt, clock
def _mission(rt):
    return rt.submit_external_mission(
        "Grow the real X audience to 5,000 followers within 30 days",
        metric="x.followers",
        baseline=2000,
        target=5000,
        deadline=NOW + timedelta(days=30),
        direction=MissionDirection.AT_LEAST,
        constraints=[
            "obey applicable law",
            "obey X platform policy",
            "preserve account survival",
            "protect long-term reputation",
            "respect resource budget",
        ],
        evidence_refs=["x-baseline:2000"],
    )


def test_external_mission_is_long_horizon_and_pressure_tracks_pace(tmp_path):
    rt, clock = _runtime(tmp_path)
    mission = _mission(rt)
    goal = next(g for g in rt.goals if g.goal_id == mission.goal_id)

    initial = rt.mission_progress(mission.mission_id)
    assert mission.status is MissionStatus.ACTIVE
    assert goal.status is GoalStatus.ACTIVE
    assert goal.auto_complete is False
    assert initial.current_value == 2000
    assert initial.trajectory == "UNKNOWN"

    clock.advance(timedelta(days=10).total_seconds())
    rt.observe_mission_metric(
        mission.mission_id,
        value=2100,
        source="x-public-profile",
        evidence_refs=["x-snapshot:day10"],
    )
    behind = rt.mission_progress(mission.mission_id)
    assert behind.trajectory == "BEHIND"
    assert behind.pressure > initial.pressure
    assert behind.exploration_rate > initial.exploration_rate
    assert goal.priority > 0.55

    rt.observe_mission_metric(
        mission.mission_id,
        value=5000,
        source="x-public-profile",
        evidence_refs=["x-snapshot:target"],
    )
    assert mission.status is MissionStatus.SUCCEEDED
    assert goal.status is GoalStatus.COMPLETED
    assert rt.mission_progress(mission.mission_id).pressure == 0.0


def test_deadline_failure_is_a_valid_terminal_outcome(tmp_path):
    rt, clock = _runtime(tmp_path)
    mission = _mission(rt)
    goal = next(g for g in rt.goals if g.goal_id == mission.goal_id)

    clock.advance(timedelta(days=31).total_seconds())
    progress = rt.mission_progress(mission.mission_id)

    assert progress.status is MissionStatus.FAILED
    assert progress.current_value == 2000
    assert goal.status is GoalStatus.FAILED
    assert mission.completed_at == mission.deadline


def test_target_observed_after_deadline_does_not_turn_failure_into_success(tmp_path):
    rt, clock = _runtime(tmp_path)
    mission = _mission(rt)

    clock.advance(timedelta(days=31).total_seconds())
    rt.observe_mission_metric(
        mission.mission_id,
        value=5000,
        source="x-public-profile",
        evidence_refs=["x-snapshot:late-target"],
    )

    assert mission.status is MissionStatus.FAILED
    assert mission.completed_at == mission.deadline


def test_mission_persists_across_restart(tmp_path):
    rt, clock = _runtime(tmp_path)
    mission = _mission(rt)
    clock.advance(timedelta(days=3).total_seconds())
    rt.observe_mission_metric(
        mission.mission_id,
        value=2234,
        source="x-public-profile",
        evidence_refs=["x-snapshot:2234"],
    )
    rt.persist()

    restored = AgentRuntime.restore(tmp_path, clock=FrozenClock(clock.now()))
    restored_mission = restored.missions.get(mission.mission_id)
    progress = restored.mission_progress(mission.mission_id)

    assert restored_mission.goal_id == mission.goal_id
    assert progress.current_value == 2234
    assert progress.status is MissionStatus.ACTIVE
    assert "obey X platform policy" in restored_mission.constraints


def test_settled_experiment_updates_metric_and_hypothesis_modestly(tmp_path):
    rt, clock = _runtime(tmp_path)
    mission = _mission(rt)
    hypothesis = rt.learning.hypothesize(
        "Publishing concrete build logs may improve follower conversion",
        evidence_refs=[mission.mission_id],
        confidence=0.5,
    )
    experiment = rt.missions.propose_experiment(
        mission.mission_id,
        strategy="Publish one evidence-rich build log and measure follower change.",
        expected_effect="Increase qualified follower conversion over the measurement window.",
        proposed_at=clock.now(),
        hypothesis_id=hypothesis.hypothesis_id,
        evidence_refs=[mission.mission_id],
    )
    rt.start_mission_experiment(experiment.experiment_id)
    with pytest.raises(ValueError, match="requires evidence"):
        rt.settle_mission_experiment(
            experiment.experiment_id,
            result_value=2040,
            conclusion=MissionConclusion.SUPPORTED,
            evidence_refs=[],
        )

    settled = rt.settle_mission_experiment(
        experiment.experiment_id,
        result_value=2040,
        conclusion=MissionConclusion.SUPPORTED,
        evidence_refs=["x-analytics:experiment-1"],
        notes="Observed after the bounded measurement window.",
    )

    assert settled.status is MissionExperimentStatus.SETTLED
    assert settled.before_value == 2000
    assert settled.after_value == 2040
    assert hypothesis.confidence == pytest.approx(0.58)
    assert "x-analytics:experiment-1" in hypothesis.evidence_refs
    assert rt.mission_progress(mission.mission_id).current_value == 2040


def test_only_one_running_experiment_per_mission(tmp_path):
    rt, clock = _runtime(tmp_path)
    mission = _mission(rt)
    first = rt.missions.propose_experiment(
        mission.mission_id,
        strategy="Test one format.",
        expected_effect="Measure a change.",
        proposed_at=clock.now(),
    )
    second = rt.missions.propose_experiment(
        mission.mission_id,
        strategy="Test a second format.",
        expected_effect="Measure another change.",
        proposed_at=clock.now(),
    )
    rt.start_mission_experiment(first.experiment_id)
    with pytest.raises(ValueError, match="already has a running experiment"):
        rt.start_mission_experiment(second.experiment_id)


def _advice():
    return json.dumps(
        {
            "statement": (
                "Concrete build logs with measurable results may convert technical "
                "readers into followers better than generic AI commentary."
            ),
            "strategy": (
                "Publish one concise build log with a concrete result and compare "
                "profile visits and follower change against the prior content window."
            ),
            "expected_effect": "Higher follower conversion with bounded posting volume.",
            "risks": [
                "Over-posting can reduce audience quality",
                "Generic replies can look spammy",
            ],
            "confidence": 0.9,
        }
    )


def test_mission_deliberation_creates_bounded_learning_experiment(tmp_path):
    backend = FixtureAgentBackend(_advice())
    rt, _clock = _runtime(tmp_path, backend=backend)
    mission = _mission(rt)

    experiment = asyncio.run(rt.deliberate_external_mission(mission.mission_id))
    repeated = asyncio.run(rt.deliberate_external_mission(mission.mission_id))
    assert repeated.experiment_id == experiment.experiment_id
    assert experiment.status is MissionExperimentStatus.PROPOSED
    assert experiment.hypothesis_id is not None
    hypothesis = next(
        h for h in rt.learning.hypotheses if h.hypothesis_id == experiment.hypothesis_id
    )
    assert hypothesis.confidence == 0.65
    assert len(backend.requests) == 1

    request = backend.requests[0]
    assert request.policy.allowed_tools == frozenset()
    assert request.policy.allowed_networks == frozenset()
    assert request.max_duration_seconds == 60.0
    assert request.context["constraints"]["execution_forbidden"] is True
    assert (
        request.context["constraints"]["law_and_platform_policy_are_non_negotiable"]
        is True
    )
    assert request.context["mission"]["target"] == 5000
    assert "obey X platform policy" in request.context["mission"]["constraints"]


def test_mission_pressure_enters_motivation_without_overriding_viability(tmp_path):
    rt, clock = _runtime(tmp_path)
    mission = _mission(rt)
    clock.advance(timedelta(days=15).total_seconds())
    rt.observe_mission_metric(
        mission.mission_id,
        value=2200,
        source="x-public-profile",
        evidence_refs=["x-snapshot:day15"],
    )
    observation = Observation.self_and_world(
        self_metrics={
            "operational_integrity": 0.9,
            "compute": 0.8,
            "memory_integrity": 0.9,
        },
        world={},
        external_task=True,
        timestamp=clock.now(),
    )
    result = rt.step(observation)

    progress = rt.mission_progress(mission.mission_id)
    assert result.motivation.drives["external_mission"] == pytest.approx(
        progress.pressure
    )
    assert result.motivation.exploration_bias >= min(
        0.90, 0.15 + progress.pressure * 0.65
    )
    goal = next(g for g in rt.goals if g.goal_id == mission.goal_id)
    assert goal.priority < 0.8


def test_invalid_mission_bounds_are_rejected_without_creating_goal(tmp_path):
    rt, _clock = _runtime(tmp_path)
    before = len(rt.goals)
    with pytest.raises(ValueError, match="AT_LEAST"):
        rt.submit_external_mission(
            "Impossible direction",
            metric="metric",
            baseline=10,
            target=5,
            deadline=NOW + timedelta(days=1),
            direction=MissionDirection.AT_LEAST,
        )
    assert len(rt.goals) == before

def _roadmap_steps():
    return [
        {
            "key": "research",
            "title": "Research",
            "objective": "Collect current evidence and candidate tactics.",
            "estimated_hours": 2,
            "depends_on": [],
            "resource_tags": ["reasoning"],
            "exit_criteria": "Evidence set is recorded.",
        },
        {
            "key": "instrument",
            "title": "Instrument",
            "objective": "Prepare measurement and observation hooks.",
            "estimated_hours": 1,
            "depends_on": [],
            "resource_tags": ["engineering"],
            "exit_criteria": "Metrics can be observed prospectively.",
        },
        {
            "key": "trial",
            "title": "Run trial",
            "objective": "Run one bounded evidence-backed experiment.",
            "estimated_hours": 3,
            "depends_on": ["research", "instrument"],
            "resource_tags": ["execution"],
            "exit_criteria": "Trial outcome is measurable.",
        },
    ]

def test_mission_roadmap_schedules_independent_steps_in_parallel(tmp_path):
    rt, _clock = _runtime(tmp_path)
    mission = _mission(rt)
    roadmap = rt.set_mission_roadmap(
        mission.mission_id,
        steps=_roadmap_steps(),
        rationale="Research and instrumentation can proceed independently.",
    )

    schedule = rt.mission_portfolio_schedule(max_parallelism=2)
    by_title = {item.title: item for item in schedule.items}

    assert roadmap.version == 1
    assert by_title["Research"].status is MissionStepStatus.READY
    assert by_title["Instrument"].status is MissionStepStatus.READY
    assert by_title["Research"].planned_start == NOW
    assert by_title["Instrument"].planned_start == NOW
    assert by_title["Research"].lane != by_title["Instrument"].lane
    assert by_title["Run trial"].status is MissionStepStatus.PLANNED
    assert by_title["Run trial"].planned_start == max(
        by_title["Research"].planned_finish,
        by_title["Instrument"].planned_finish,
    )

def test_shared_resource_serializes_otherwise_parallel_steps(tmp_path):
    rt, _clock = _runtime(tmp_path)
    mission = _mission(rt)
    steps = _roadmap_steps()
    steps[1]["resource_tags"] = ["reasoning"]
    rt.set_mission_roadmap(mission.mission_id, steps=steps)

    schedule = rt.mission_portfolio_schedule(max_parallelism=3)
    first = next(item for item in schedule.items if item.title == "Research")
    second = next(item for item in schedule.items if item.title == "Instrument")

    assert first.planned_finish <= second.planned_start or (
        second.planned_finish <= first.planned_start
    )


def test_step_dependencies_require_completion_evidence(tmp_path):
    rt, _clock = _runtime(tmp_path)
    mission = _mission(rt)
    roadmap = rt.set_mission_roadmap(mission.mission_id, steps=_roadmap_steps())
    research = next(step for step in roadmap.steps if step.title == "Research")
    trial = next(step for step in roadmap.steps if step.title == "Run trial")

    with pytest.raises(ValueError, match="dependency"):
        rt.start_mission_step(trial.step_id)

    rt.start_mission_step(research.step_id)
    with pytest.raises(ValueError, match="requires evidence"):
        rt.complete_mission_step(research.step_id, evidence_refs=[])
    rt.complete_mission_step(research.step_id, evidence_refs=["evidence:research"])

    assert research.completed_at == NOW
    assert "evidence:research" in research.evidence_refs


def test_mission_level_dependency_creates_serial_portfolio_order(tmp_path):
    rt, _clock = _runtime(tmp_path)
    first = _mission(rt)
    second = rt.submit_external_mission(
        "Reach a second measurable objective",
        metric="secondary.metric",
        baseline=0,
        target=10,
        deadline=NOW + timedelta(days=40),
        depends_on_mission_ids=[first.mission_id],
    )
    rt.set_mission_roadmap(
        first.mission_id,
        steps=[{
            "key": "first",
            "title": "First mission work",
            "objective": "Complete the predecessor work.",
            "estimated_hours": 4,
            "depends_on": [],
            "resource_tags": ["execution"],
            "exit_criteria": "Predecessor work is evidenced.",
        }],
    )
    rt.set_mission_roadmap(
        second.mission_id,
        steps=[{
            "key": "second",
            "title": "Second mission work",
            "objective": "Begin only after the predecessor plan.",
            "estimated_hours": 2,
            "depends_on": [],
            "resource_tags": ["analysis"],
            "exit_criteria": "Dependent work is evidenced.",
        }],
    )

    schedule = rt.mission_portfolio_schedule(max_parallelism=2)
    first_item = next(item for item in schedule.items if item.mission_id == first.mission_id)
    second_item = next(item for item in schedule.items if item.mission_id == second.mission_id)

    assert second_item.planned_start >= first_item.planned_finish
    with pytest.raises(ValueError, match="mission dependency"):
        rt.start_mission_step(second_item.step_id)

    rt.start_mission_step(first_item.step_id)
    rt.complete_mission_step(first_item.step_id, evidence_refs=["first:done"])
    after_plan = rt.mission_portfolio_schedule(max_parallelism=2)
    second_after = next(
        item for item in after_plan.items if item.mission_id == second.mission_id
    )
    assert second_after.status is MissionStepStatus.BLOCKED
    assert second_after.blocked_by == (
        "await-mission-success:" + first.mission_id,
    )


def test_roadmap_persists_and_completed_plan_requests_revision(tmp_path):
    rt, _clock = _runtime(tmp_path)
    mission = _mission(rt)
    roadmap = rt.set_mission_roadmap(
        mission.mission_id,
        steps=[{
            "key": "one",
            "title": "One bounded step",
            "objective": "Produce one evidenced result.",
            "estimated_hours": 1,
            "depends_on": [],
            "resource_tags": [],
            "exit_criteria": "Evidence exists.",
        }],
    )
    step = roadmap.steps[0]
    rt.start_mission_step(step.step_id)
    rt.complete_mission_step(step.step_id, evidence_refs=["step:evidence"])
    assert rt.missions.roadmap_needs_revision(mission.mission_id) is True
    rt.persist()

    restored = AgentRuntime.restore(tmp_path, clock=FrozenClock(NOW))
    restored_roadmap = restored.missions.latest_roadmap(mission.mission_id)

    assert restored_roadmap is not None
    assert restored_roadmap.version == 1
    assert restored_roadmap.steps[0].completed_at == NOW
    assert restored.missions.roadmap_needs_revision(mission.mission_id) is True

def _roadmap_advice():
    return json.dumps({
        "rationale": "Parallelize evidence collection and instrumentation, then converge.",
        "steps": [
            {
                "key": "evidence",
                "title": "Collect evidence",
                "objective": "Collect current external evidence.",
                "estimated_hours": 2,
                "depends_on": [],
                "resource_tags": ["reasoning"],
                "exit_criteria": "A dated evidence set exists.",
            },
            {
                "key": "metrics",
                "title": "Prepare metrics",
                "objective": "Prepare prospective measurement.",
                "estimated_hours": 1,
                "depends_on": [],
                "resource_tags": ["engineering"],
                "exit_criteria": "Metric observations can be recorded.",
            },
            {
                "key": "experiment",
                "title": "Run experiment",
                "objective": "Run one bounded experiment.",
                "estimated_hours": 4,
                "depends_on": ["evidence", "metrics"],
                "resource_tags": ["execution"],
                "exit_criteria": "A prospective outcome is recorded.",
            },
        ],
    })


def test_advisory_backend_can_create_serial_parallel_roadmap(tmp_path):
    backend = FixtureAgentBackend(_roadmap_advice())
    rt, _clock = _runtime(tmp_path, backend=backend)
    mission = _mission(rt)

    roadmap = asyncio.run(rt.deliberate_mission_roadmap(mission.mission_id))
    repeated = asyncio.run(rt.deliberate_mission_roadmap(mission.mission_id))

    assert repeated.roadmap_id == roadmap.roadmap_id
    assert len(backend.requests) == 1
    assert len(roadmap.steps) == 3
    experiment = next(step for step in roadmap.steps if step.title == "Run experiment")
    assert len(experiment.depends_on_step_ids) == 2
    schedule = rt.mission_portfolio_schedule(max_parallelism=2)
    ready = [item for item in schedule.items if item.status is MissionStepStatus.READY]
    assert {item.title for item in ready} == {"Collect evidence", "Prepare metrics"}
    request = backend.requests[0]
    assert request.policy.allowed_tools == frozenset()
    assert request.max_duration_seconds == 60.0
    assert request.token_budget == 1800
    assert request.output_schema["properties"]["steps"]["maxItems"] == 8
    assert request.context["planning_rules"]["steps_may_be_serial_or_parallel"] is True


def test_active_roadmap_can_be_forced_to_replan_with_capability_context(tmp_path):
    backend = FixtureAgentBackend(_roadmap_advice())
    rt, _clock = _runtime(tmp_path, backend=backend)
    mission = _mission(rt)

    first = asyncio.run(rt.deliberate_mission_roadmap(mission.mission_id))
    revised = asyncio.run(
        rt.deliberate_mission_roadmap(
            mission.mission_id,
            force_revision=True,
            planning_context={
                "measurement": {
                    "post_level_follower_attribution": "UNAVAILABLE",
                    "prospective_follower_snapshots": "AVAILABLE",
                }
            },
            evidence_refs=["capability:x-metrics:v1"],
        )
    )

    assert first.version == 1
    assert revised.version == 2
    assert revised.roadmap_id != first.roadmap_id
    assert "capability:x-metrics:v1" in revised.evidence_refs
    assert len(backend.requests) == 2
    request = backend.requests[-1]
    assert request.context["revision_requested"] is True
    assert request.context["current_roadmap"]["version"] == 1
    assert (
        request.context["application_context"]["measurement"][
            "post_level_follower_attribution"
        ]
        == "UNAVAILABLE"
    )
    assert (
        request.context["planning_rules"][
            "do_not_require_unavailable_evidence_or_capabilities"
        ]
        is True
    )
