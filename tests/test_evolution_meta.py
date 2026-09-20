from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_runtime.clock import FrozenClock
from agent_runtime.evolution import (
    EvidenceClass,
    EvidenceMetric,
    ExperimentArm,
    ExperimentMetric,
    ExperimentResult,
    MetricDirection,
)
from agent_runtime.runtime import AgentRuntime


NOW = datetime(2026, 9, 20, 2, 0, tzinfo=timezone.utc)


def _world(rt: AgentRuntime, label: str):
    rt.step()
    experience = rt.evolution_experiences[-1]
    evidence = rt.record_evolution_evidence(
        experience.experience_id,
        evidence_class=EvidenceClass.WORLD_OBSERVED,
        settled_at=rt.clock.now(),
        evidence_refs=[f"world:{label}"],
    )
    return experience, evidence


def _promote_meta(rt: AgentRuntime, policy_name: str, value: float, label: str):
    _, seed = _world(rt, f"{label}:seed")
    change = rt.propose_meta_evolution_change(
        policy_name=policy_name,
        proposed_value=value,
        evidence_refs=[seed.evidence_id],
    )
    candidate = next(
        item
        for item in rt.evolution_candidates
        if item.candidate_id == change.candidate_id
    )
    start = rt.clock.now() + timedelta(minutes=1)
    experiment = rt.create_evolution_experiment(
        candidate.candidate_id,
        prospective_start_at=start,
        prospective_end_at=start + timedelta(hours=3),
        metrics=[
            ExperimentMetric(
                "learning_quality",
                MetricDirection.HIGHER_BETTER,
                tolerance=0.01,
                primary=True,
            )
        ],
        min_samples_per_arm=1,
        regression_evidence_refs=[seed.evidence_id],
    )
    rt.validate_evolution_replay(
        experiment.spec.experiment_id,
        evidence_refs=[f"replay:{label}"],
    )
    rt.enter_evolution_shadow(
        experiment.spec.experiment_id,
        evidence_refs=[f"shadow:{label}"],
    )
    rt.clock.advance(60)
    rt.begin_evolution_prospective_trial(experiment.spec.experiment_id)

    base_exp, base_ev = _world(rt, f"{label}:baseline")
    rt.add_evolution_trial_sample(
        experiment.spec.experiment_id,
        arm=ExperimentArm.BASELINE,
        experience_id=base_exp.experience_id,
        evidence_refs=[base_ev.evidence_id],
        known_at=rt.clock.now() + timedelta(minutes=5),
        metric_values={"learning_quality": 1.0},
    )
    rt.clock.advance(60)
    cand_exp, cand_ev = _world(rt, f"{label}:candidate")
    rt.add_evolution_trial_sample(
        experiment.spec.experiment_id,
        arm=ExperimentArm.CANDIDATE,
        experience_id=cand_exp.experience_id,
        evidence_refs=[cand_ev.evidence_id],
        known_at=rt.clock.now() + timedelta(minutes=5),
        metric_values={"learning_quality": 2.0},
    )
    evaluation = rt.evaluate_evolution_experiment(
        experiment.spec.experiment_id
    )
    assert evaluation.result is ExperimentResult.IMPROVEMENT_OBSERVED
    version = rt.promote_meta_evolution_change(
        change.change_id,
        experiment_id=experiment.spec.experiment_id,
        evaluation_id=evaluation.evaluation_id,
    )
    return change, version


def test_meta_policy_is_allowlisted_and_bounded_before_candidate_creation(tmp_path):
    rt = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    _, evidence = _world(rt, "seed")
    before = list(rt.evolution_candidates)

    with pytest.raises(PermissionError, match="not allowlisted"):
        rt.propose_meta_evolution_change(
            policy_name="governance.authorization_threshold",
            proposed_value=0.0,
            evidence_refs=[evidence.evidence_id],
        )
    assert rt.evolution_candidates == before

    with pytest.raises(ValueError, match="outside allowed bounds"):
        rt.propose_meta_evolution_change(
            policy_name="experiment.min_samples_per_arm",
            proposed_value=0,
            evidence_refs=[evidence.evidence_id],
        )
    assert rt.evolution_candidates == before


def test_meta_change_uses_standard_candidate_experiment_and_capability_protocol(tmp_path):
    clock = FrozenClock(NOW)
    rt = AgentRuntime.bootstrap(tmp_path, clock=clock)
    change, version = _promote_meta(
        rt,
        "experiment.min_samples_per_arm",
        2,
        "meta-v1",
    )

    assert version.value == 2.0
    assert version.candidate_id == change.candidate_id
    capability_id = rt.evolution_meta_registry.capability_id(
        "experiment.min_samples_per_arm"
    )
    active_capability = rt.evolution_capability_registry.active(capability_id)
    assert active_capability.candidate_id == change.candidate_id
    assert rt.evolution_meta_registry.current(
        "experiment.min_samples_per_arm", 3.0
    ) == 2.0

    restored = AgentRuntime.restore(tmp_path, clock=clock)
    assert restored.evolution_meta_versions == [version]


def test_meta_policy_changes_future_experiment_default(tmp_path):
    clock = FrozenClock(NOW)
    rt = AgentRuntime.bootstrap(tmp_path, clock=clock)
    _promote_meta(
        rt,
        "experiment.min_samples_per_arm",
        2,
        "meta-default",
    )
    _, seed = _world(rt, "next-seed")
    candidate = rt.propose_evolution_candidate(
        requirement="JUDGMENT_RULE",
        target="content-strategy:next",
        description="next trial",
        baseline_ref="strategy:old",
        rollback_plan="restore strategy:old",
        evidence_refs=[seed.evidence_id],
    )
    start = clock.now() + timedelta(minutes=1)
    experiment = rt.create_evolution_experiment(
        candidate.candidate_id,
        prospective_start_at=start,
        prospective_end_at=start + timedelta(hours=1),
        metrics=[
            ExperimentMetric(
                "fitness",
                MetricDirection.HIGHER_BETTER,
                primary=True,
            )
        ],
        regression_evidence_refs=[seed.evidence_id],
    )
    assert experiment.spec.min_samples_per_arm == 2


def test_second_meta_policy_version_can_rollback_to_prior_promoted_version(tmp_path):
    clock = FrozenClock(NOW)
    rt = AgentRuntime.bootstrap(tmp_path, clock=clock)
    _, first = _promote_meta(
        rt,
        "experiment.min_samples_per_arm",
        2,
        "meta-first",
    )
    clock.advance(60)
    _, second = _promote_meta(
        rt,
        "experiment.min_samples_per_arm",
        4,
        "meta-second",
    )
    assert second.value == 4.0
    restored = rt.rollback_meta_evolution_policy(
        "experiment.min_samples_per_arm",
        reason="prospective regression after activation",
        evidence_refs=["world:meta-regression"],
    )
    assert restored.version_id == first.version_id
    assert restored.value == 2.0
    assert rt.evolution_meta_registry.current(
        "experiment.min_samples_per_arm", 3.0
    ) == 2.0


def test_meta_consolidation_threshold_changes_sleep_behavior(tmp_path):
    clock = FrozenClock(NOW)
    rt = AgentRuntime.bootstrap(tmp_path, clock=clock)
    _promote_meta(
        rt,
        "consolidation.min_recurrence",
        3,
        "meta-sleep",
    )
    for label in ("repeat-a", "repeat-b"):
        rt.step()
        experience = rt.evolution_experiences[-1]
        rt.record_evolution_evidence(
            experience.experience_id,
            evidence_class=EvidenceClass.WORLD_OBSERVED,
            settled_at=clock.now(),
            metrics=[EvidenceMetric("sleep_signal", 1.0)],
            evidence_refs=[f"world:{label}"],
        )
    report = rt.consolidate_evolution()
    repeated_world = [
        row for row in report.clusters
        if "sleep_signal" in row.signature
    ]
    assert len(repeated_world) == 1
    assert repeated_world[0].recurring is False
