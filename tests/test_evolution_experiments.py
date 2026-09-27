from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_runtime.clock import FrozenClock
from agent_runtime.evolution import (
    EvidenceClass,
    ExperimentArm,
    ExperimentMetric,
    ExperimentResult,
    ExperimentStage,
    MetricDirection,
    MutationRequirement,
)
from agent_runtime.runtime import AgentRuntime


NOW = datetime(2026, 9, 19, 18, 0, tzinfo=timezone.utc)


def _candidate(rt: AgentRuntime):
    rt.step()
    experience = rt.evolution_experiences[-1]
    evidence = rt.record_evolution_evidence(
        experience.experience_id,
        evidence_class=EvidenceClass.WORLD_OBSERVED,
        settled_at=rt.clock.now(),
        evidence_refs=["world:baseline-seed"],
    )
    candidate = rt.propose_evolution_candidate(
        requirement=MutationRequirement.JUDGMENT_RULE,
        target="content-strategy",
        description="use evidence-backed strategy challenger",
        baseline_ref="strategy:v1",
        rollback_plan="restore strategy:v1",
        evidence_refs=[evidence.evidence_id],
    )
    return candidate, evidence


def _metrics():
    return [
        ExperimentMetric(
            "engagement",
            MetricDirection.HIGHER_BETTER,
            tolerance=0.01,
            primary=True,
        ),
        ExperimentMetric(
            "policy_errors",
            MetricDirection.LOWER_BETTER,
            tolerance=0.0,
        ),
    ]


def _start_trial(rt: AgentRuntime, candidate, seed_evidence):
    start = NOW + timedelta(hours=1)
    end = start + timedelta(hours=24)
    experiment = rt.create_evolution_experiment(
        candidate.candidate_id,
        prospective_start_at=start,
        prospective_end_at=end,
        metrics=_metrics(),
        min_samples_per_arm=2,
        regression_evidence_refs=[seed_evidence.evidence_id],
        stop_conditions=["policy regression"],
    )
    rt.validate_evolution_replay(
        experiment.spec.experiment_id,
        evidence_refs=["replay:passed"],
    )
    rt.enter_evolution_shadow(
        experiment.spec.experiment_id,
        evidence_refs=["shadow:passed"],
    )
    rt.clock.advance(3600)
    rt.begin_evolution_prospective_trial(experiment.spec.experiment_id)
    return experiment


def _sample(rt: AgentRuntime, experiment_id: str, arm, engagement, policy_errors=0.0):
    rt.step()
    experience = rt.evolution_experiences[-1]
    settled = rt.clock.now() + timedelta(minutes=5)
    evidence = rt.record_evolution_evidence(
        experience.experience_id,
        evidence_class=EvidenceClass.WORLD_OBSERVED,
        settled_at=settled,
        evidence_refs=[f"world:{experience.experience_id}"],
    )
    sample = rt.add_evolution_trial_sample(
        experiment_id,
        arm=arm,
        experience_id=experience.experience_id,
        evidence_refs=[evidence.evidence_id],
        known_at=settled,
        metric_values={
            "engagement": engagement,
            "policy_errors": policy_errors,
        },
    )
    rt.clock.advance(600)
    return sample


def test_experiment_enforces_replay_shadow_prospective_order(tmp_path):
    clock = FrozenClock(NOW)
    rt = AgentRuntime.bootstrap(tmp_path, clock=clock)
    candidate, seed = _candidate(rt)
    start = NOW + timedelta(hours=1)
    experiment = rt.create_evolution_experiment(
        candidate.candidate_id,
        prospective_start_at=start,
        prospective_end_at=start + timedelta(hours=4),
        metrics=_metrics(),
        regression_evidence_refs=[seed.evidence_id],
    )
    with pytest.raises(ValueError, match="SHADOW"):
        rt.begin_evolution_prospective_trial(experiment.spec.experiment_id)
    rt.validate_evolution_replay(
        experiment.spec.experiment_id,
        evidence_refs=["replay:1"],
    )
    rt.enter_evolution_shadow(
        experiment.spec.experiment_id,
        evidence_refs=["shadow:1"],
    )
    with pytest.raises(ValueError, match="before frozen window"):
        rt.begin_evolution_prospective_trial(experiment.spec.experiment_id)
    clock.advance(3600)
    item = rt.begin_evolution_prospective_trial(experiment.spec.experiment_id)
    assert item.stage is ExperimentStage.PROSPECTIVE_TRIAL


def test_prospective_sample_rejects_future_evidence_leakage(tmp_path):
    clock = FrozenClock(NOW)
    rt = AgentRuntime.bootstrap(tmp_path, clock=clock)
    candidate, seed = _candidate(rt)
    experiment = _start_trial(rt, candidate, seed)
    rt.step()
    experience = rt.evolution_experiences[-1]
    evidence = rt.record_evolution_evidence(
        experience.experience_id,
        evidence_class=EvidenceClass.WORLD_OBSERVED,
        settled_at=clock.now() + timedelta(hours=2),
        evidence_refs=["future:outcome"],
    )
    with pytest.raises(ValueError, match="leaks evidence"):
        rt.add_evolution_trial_sample(
            experiment.spec.experiment_id,
            arm=ExperimentArm.BASELINE,
            experience_id=experience.experience_id,
            evidence_refs=[evidence.evidence_id],
            known_at=clock.now() + timedelta(hours=1),
            metric_values={"engagement": 1.0, "policy_errors": 0.0},
        )


def test_prospective_comparison_observes_improvement_and_persists(tmp_path):
    clock = FrozenClock(NOW)
    rt = AgentRuntime.bootstrap(tmp_path, clock=clock)
    candidate, seed = _candidate(rt)
    experiment = _start_trial(rt, candidate, seed)
    exp_id = experiment.spec.experiment_id

    _sample(rt, exp_id, ExperimentArm.BASELINE, 1.0)
    _sample(rt, exp_id, ExperimentArm.CANDIDATE, 1.5)
    _sample(rt, exp_id, ExperimentArm.BASELINE, 1.1)
    _sample(rt, exp_id, ExperimentArm.CANDIDATE, 1.6)

    evaluation = rt.evaluate_evolution_experiment(exp_id)
    assert evaluation.result is ExperimentResult.IMPROVEMENT_OBSERVED
    assert evaluation.improved_metrics == ("engagement",)
    assert evaluation.regressed_metrics == ()
    restored = AgentRuntime.restore(tmp_path, clock=clock)
    [restored_experiment] = restored.evolution_experiments
    assert restored_experiment.evaluations[-1] == evaluation


def test_regression_constraint_rejects_candidate_even_if_primary_improves(tmp_path):
    clock = FrozenClock(NOW)
    rt = AgentRuntime.bootstrap(tmp_path, clock=clock)
    candidate, seed = _candidate(rt)
    experiment = _start_trial(rt, candidate, seed)
    exp_id = experiment.spec.experiment_id

    _sample(rt, exp_id, ExperimentArm.BASELINE, 1.0, 0.0)
    _sample(rt, exp_id, ExperimentArm.CANDIDATE, 2.0, 1.0)
    _sample(rt, exp_id, ExperimentArm.BASELINE, 1.0, 0.0)
    _sample(rt, exp_id, ExperimentArm.CANDIDATE, 2.0, 1.0)

    evaluation = rt.evaluate_evolution_experiment(exp_id)
    assert evaluation.result is ExperimentResult.REGRESSION_OBSERVED
    assert evaluation.regressed_metrics == ("policy_errors",)


def test_insufficient_samples_remain_inconclusive(tmp_path):
    clock = FrozenClock(NOW)
    rt = AgentRuntime.bootstrap(tmp_path, clock=clock)
    candidate, seed = _candidate(rt)
    experiment = _start_trial(rt, candidate, seed)
    exp_id = experiment.spec.experiment_id
    _sample(rt, exp_id, ExperimentArm.BASELINE, 1.0)
    _sample(rt, exp_id, ExperimentArm.CANDIDATE, 2.0)
    evaluation = rt.evaluate_evolution_experiment(exp_id)
    assert evaluation.result is ExperimentResult.INSUFFICIENT_EVIDENCE


@pytest.mark.parametrize(
    "stage",
    [ExperimentStage.PROPOSED, ExperimentStage.REPLAY_VALIDATED, ExperimentStage.SHADOW],
)
def test_abort_closes_preprospective_experiment_durably(tmp_path, stage):
    clock = FrozenClock(NOW)
    rt = AgentRuntime.bootstrap(tmp_path, clock=clock)
    candidate, seed = _candidate(rt)
    start = NOW + timedelta(hours=1)
    experiment = rt.create_evolution_experiment(
        candidate.candidate_id,
        prospective_start_at=start,
        prospective_end_at=start + timedelta(hours=4),
        metrics=_metrics(),
        regression_evidence_refs=[seed.evidence_id],
    )
    if stage in {ExperimentStage.REPLAY_VALIDATED, ExperimentStage.SHADOW}:
        rt.validate_evolution_replay(
            experiment.spec.experiment_id,
            evidence_refs=["replay:1"],
        )
    if stage is ExperimentStage.SHADOW:
        rt.enter_evolution_shadow(
            experiment.spec.experiment_id,
            evidence_refs=["shadow:1"],
        )

    aborted = rt.abort_evolution_experiment(
        experiment.spec.experiment_id,
        evidence_refs=["policy-drift:1"],
    )
    assert aborted.stage is ExperimentStage.CLOSED
    assert aborted.transitions[-1].from_stage is stage
    assert aborted.transitions[-1].to_stage is ExperimentStage.CLOSED
    assert aborted.transitions[-1].evidence_refs == ("policy-drift:1",)

    restored = AgentRuntime.restore(tmp_path, clock=clock)
    assert (
        restored.evolution_experiment_manager.get(experiment.spec.experiment_id).stage
        is ExperimentStage.CLOSED
    )
