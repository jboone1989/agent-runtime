from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_runtime.clock import FrozenClock
from agent_runtime.evolution import (
    CapabilityState,
    EvidenceClass,
    ExperimentArm,
    ExperimentMetric,
    ExperimentResult,
    MetricDirection,
    MutationRequirement,
)
from agent_runtime.runtime import AgentRuntime


NOW = datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc)


def _world_evidence(rt: AgentRuntime, label: str):
    rt.step()
    experience = rt.evolution_experiences[-1]
    evidence = rt.record_evolution_evidence(
        experience.experience_id,
        evidence_class=EvidenceClass.WORLD_OBSERVED,
        settled_at=rt.clock.now(),
        evidence_refs=[f"world:{label}"],
    )
    return experience, evidence
def _improving_candidate(rt: AgentRuntime, label: str, baseline_ref: str):
    _, seed = _world_evidence(rt, f"{label}:seed")
    candidate = rt.propose_evolution_candidate(
        requirement=MutationRequirement.JUDGMENT_RULE,
        target=f"content-strategy:{label}",
        description=f"candidate {label}",
        baseline_ref=baseline_ref,
        rollback_plan=f"restore {baseline_ref}",
        evidence_refs=[seed.evidence_id],
    )
    start = rt.clock.now() + timedelta(minutes=1)
    experiment = rt.create_evolution_experiment(
        candidate.candidate_id,
        prospective_start_at=start,
        prospective_end_at=start + timedelta(hours=4),
        metrics=[
            ExperimentMetric(
                "fitness",
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

    base_exp, base_ev = _world_evidence(rt, f"{label}:baseline")
    rt.add_evolution_trial_sample(
        experiment.spec.experiment_id,
        arm=ExperimentArm.BASELINE,
        experience_id=base_exp.experience_id,
        evidence_refs=[base_ev.evidence_id],
        known_at=rt.clock.now() + timedelta(minutes=5),
        metric_values={"fitness": 1.0},
    )
    rt.clock.advance(60)
    cand_exp, cand_ev = _world_evidence(rt, f"{label}:candidate")
    rt.add_evolution_trial_sample(
        experiment.spec.experiment_id,
        arm=ExperimentArm.CANDIDATE,
        experience_id=cand_exp.experience_id,
        evidence_refs=[cand_ev.evidence_id],
        known_at=rt.clock.now() + timedelta(minutes=5),
        metric_values={"fitness": 2.0},
    )
    evaluation = rt.evaluate_evolution_experiment(experiment.spec.experiment_id)
    assert evaluation.result is ExperimentResult.IMPROVEMENT_OBSERVED
    return candidate, experiment, evaluation


def test_promotion_requires_prospective_improvement(tmp_path):
    clock = FrozenClock(NOW)
    rt = AgentRuntime.bootstrap(tmp_path, clock=clock)
    _, seed = _world_evidence(rt, "seed")
    candidate = rt.propose_evolution_candidate(
        requirement=MutationRequirement.JUDGMENT_RULE,
        target="content-strategy:no-proof",
        description="not yet proven",
        baseline_ref="strategy:v1",
        rollback_plan="restore strategy:v1",
        evidence_refs=[seed.evidence_id],
    )
    start = clock.now() + timedelta(minutes=1)
    experiment = rt.create_evolution_experiment(
        candidate.candidate_id,
        prospective_start_at=start,
        prospective_end_at=start + timedelta(hours=2),
        metrics=[
            ExperimentMetric(
                "fitness",
                MetricDirection.HIGHER_BETTER,
                primary=True,
            )
        ],
        min_samples_per_arm=1,
        regression_evidence_refs=[seed.evidence_id],
    )
    rt.validate_evolution_replay(experiment.spec.experiment_id, evidence_refs=["replay"])
    rt.enter_evolution_shadow(experiment.spec.experiment_id, evidence_refs=["shadow"])
    clock.advance(60)
    rt.begin_evolution_prospective_trial(experiment.spec.experiment_id)
    evaluation = rt.evaluate_evolution_experiment(experiment.spec.experiment_id)
    assert evaluation.result is ExperimentResult.INSUFFICIENT_EVIDENCE
    with pytest.raises(PermissionError, match="prospective improvement"):
        rt.promote_evolution_candidate(
            capability_id="content.strategy",
            candidate_id=candidate.candidate_id,
            experiment_id=experiment.spec.experiment_id,
            evaluation_id=evaluation.evaluation_id,
        )
def test_promotion_is_versioned_observable_and_persistent(tmp_path):
    clock = FrozenClock(NOW)
    rt = AgentRuntime.bootstrap(tmp_path, clock=clock)
    candidate, experiment, evaluation = _improving_candidate(
        rt, "v2", "strategy:v1"
    )

    version = rt.promote_evolution_candidate(
        capability_id="content.strategy",
        candidate_id=candidate.candidate_id,
        experiment_id=experiment.spec.experiment_id,
        evaluation_id=evaluation.evaluation_id,
        applicability_context=["social", "content"],
        confidence=0.85,
    )

    assert version.state is CapabilityState.ACTIVE
    assert version.candidate_id == candidate.candidate_id
    assert version.supporting_evidence_refs
    assert len(rt.evolution_capability_activations) == 1
    assert rt.evolution_capability_activations[0].to_version_id == version.version_id

    restored = AgentRuntime.restore(tmp_path, clock=clock)
    assert restored.evolution_capability_versions == [version]
    assert restored.evolution_capability_activations == rt.evolution_capability_activations


def test_second_promotion_supersedes_and_rollback_restores_prior(tmp_path):
    clock = FrozenClock(NOW)
    rt = AgentRuntime.bootstrap(tmp_path, clock=clock)
    first_candidate, first_experiment, first_eval = _improving_candidate(
        rt, "v2", "strategy:v1"
    )
    first = rt.promote_evolution_candidate(
        capability_id="content.strategy",
        candidate_id=first_candidate.candidate_id,
        experiment_id=first_experiment.spec.experiment_id,
        evaluation_id=first_eval.evaluation_id,
    )
    clock.advance(60)
    second_candidate, second_experiment, second_eval = _improving_candidate(
        rt, "v3", first.version_id
    )
    second = rt.promote_evolution_candidate(
        capability_id="content.strategy",
        candidate_id=second_candidate.candidate_id,
        experiment_id=second_experiment.spec.experiment_id,
        evaluation_id=second_eval.evaluation_id,
    )

    assert first.state is CapabilityState.SUPERSEDED
    assert first.superseded_by == second.version_id
    assert second.rollback_target == first.version_id
    assert rt.evolution_capability_registry.active("content.strategy") is second

    restored = rt.rollback_evolution_capability(
        "content.strategy",
        reason="real-world regression",
        evidence_refs=["world:rollback"],
    )
    assert restored.version_id == first.version_id
    assert restored.state is CapabilityState.ACTIVE
    assert second.state is CapabilityState.ROLLED_BACK
    assert len(rt.evolution_capability_rollbacks) == 1
def test_promoted_capability_records_later_real_use(tmp_path):
    clock = FrozenClock(NOW)
    rt = AgentRuntime.bootstrap(tmp_path, clock=clock)
    candidate, experiment, evaluation = _improving_candidate(
        rt, "v2", "strategy:v1"
    )
    version = rt.promote_evolution_candidate(
        capability_id="content.strategy",
        candidate_id=candidate.candidate_id,
        experiment_id=experiment.spec.experiment_id,
        evaluation_id=evaluation.evaluation_id,
    )
    clock.advance(60)
    used = rt.record_evolution_capability_use(
        "content.strategy",
        success=True,
    )
    assert used.version_id == version.version_id
    assert used.usage_count == 1
    assert used.success_count == 1
    assert used.last_verified_at == clock.now()


def test_registered_baseline_makes_first_promotion_rollbackable(tmp_path):
    clock = FrozenClock(NOW)
    rt = AgentRuntime.bootstrap(tmp_path, clock=clock)
    baseline = rt.register_evolution_capability_baseline(
        capability_id="content.strategy",
        implementation_ref="strategy:v1",
        evidence_refs=["system:current-production"],
        applicability_context=["content"],
    )
    candidate, experiment, evaluation = _improving_candidate(
        rt, "v2-baseline", baseline.version_id
    )
    promoted = rt.promote_evolution_candidate(
        capability_id="content.strategy",
        candidate_id=candidate.candidate_id,
        experiment_id=experiment.spec.experiment_id,
        evaluation_id=evaluation.evaluation_id,
    )
    assert promoted.rollback_target == baseline.version_id
    assert baseline.state is CapabilityState.SUPERSEDED

    restored = rt.rollback_evolution_capability(
        "content.strategy",
        reason="post-promotion regression",
        evidence_refs=["world:regression"],
    )
    assert restored.version_id == baseline.version_id
    assert restored.state is CapabilityState.ACTIVE


def test_baseline_registration_is_idempotent_but_rejects_different_active_implementation(tmp_path):
    clock = FrozenClock(NOW)
    rt = AgentRuntime.bootstrap(tmp_path, clock=clock)
    first = rt.register_evolution_capability_baseline(
        capability_id="content.strategy",
        implementation_ref="strategy:v1",
    )
    again = rt.register_evolution_capability_baseline(
        capability_id="content.strategy",
        implementation_ref="strategy:v1",
    )
    assert again is first
    with pytest.raises(ValueError, match="disagrees"):
        rt.register_evolution_capability_baseline(
            capability_id="content.strategy",
            implementation_ref="strategy:other",
        )
