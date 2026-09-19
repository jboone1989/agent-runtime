from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_runtime.clock import FrozenClock
from agent_runtime.evolution import (
    CausalRelation,
    DiagnosisStatus,
    EvidenceClass,
    EvidenceMetric,
    EvidenceStatus,
    EvolutionExperience,
    build_evolution_diagnosis,
    build_evolution_evidence,
    make_cause,
)
from agent_runtime.runtime import AgentRuntime


NOW = datetime(2026, 9, 19, 15, 0, tzinfo=timezone.utc)


def _experience() -> EvolutionExperience:
    return EvolutionExperience(
        experience_id="evx:diag",
        trace_id="trace:diag",
        agent_id="agent:ferro",
        logical_cycle=1,
        observed_at=NOW,
        effective_at=NOW,
        sealed_at=NOW,
        goal_refs=("goal:1",),
        plan_ref=None,
        step_ref=None,
        decision_ref="decision:1",
        action_refs=("action:1",),
        outcome_refs=(),
        caused_by=(),
        evidence_refs=(),
        context_refs=(),
        belief_refs=(),
        version_bindings=(),
        compute_cost=0.0,
        financial_cost=0.0,
        outcome_known_at=None,
        confidence=1.0,
    )


def _world_evidence():
    item = build_evolution_evidence(
        _experience(),
        settled_at=NOW + timedelta(hours=4),
        evidence_class=EvidenceClass.WORLD_OBSERVED,
        status=EvidenceStatus.VERIFIED,
        outcome_success=True,
        metrics=[EvidenceMetric("followers_delta", 8, "followers")],
        evidence_refs=["x:snapshot:1"],
    )
    return item


def test_diagnosis_keeps_competing_causes_and_discriminating_experiment():
    world = _world_evidence()
    evidence = {world.evidence_id: world}
    content = make_cause(
        "content topic caused the lift",
        relation=CausalRelation.SUSPECTED_CAUSE,
        supporting_evidence_refs=[world.evidence_id],
        confidence=0.45,
    )
    timing = make_cause(
        "posting time caused the lift",
        relation=CausalRelation.SUSPECTED_CAUSE,
        supporting_evidence_refs=[world.evidence_id],
        confidence=0.35,
    )

    diagnosis = build_evolution_diagnosis(
        agent_id="agent:ferro",
        created_at=NOW + timedelta(hours=4),
        experience_refs=["evx:diag"],
        evidence=evidence,
        evidence_refs=[world.evidence_id],
        observed_pattern="followers increased after the post",
        causes=[content, timing],
        unknowns=["recommendation variance"],
        discriminating_experiment="hold topic fixed and vary posting time",
        applicability_scope="x-posting",
        status=DiagnosisStatus.SUFFICIENT,
        confidence=0.55,
    )

    assert len(diagnosis.causes) == 2
    assert diagnosis.unknowns == ("recommendation variance",)
    assert diagnosis.status is DiagnosisStatus.SUFFICIENT


def test_correlation_does_not_auto_upgrade_to_supported_cause():
    world = _world_evidence()
    cause = make_cause(
        "topic and outcome moved together",
        relation=CausalRelation.CORRELATION,
        supporting_evidence_refs=[world.evidence_id],
        confidence=0.8,
    )
    diagnosis = build_evolution_diagnosis(
        agent_id="agent:ferro",
        created_at=NOW,
        experience_refs=["evx:diag"],
        evidence={world.evidence_id: world},
        evidence_refs=[world.evidence_id],
        observed_pattern="topic correlated with lift",
        causes=[cause],
        status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
        confidence=0.4,
    )
    assert diagnosis.causes[0].relation is CausalRelation.CORRELATION
    assert diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE


def test_supported_cause_rejects_model_only_support():
    model = build_evolution_evidence(
        _experience(),
        settled_at=NOW,
        evidence_class=EvidenceClass.MODEL_INFERRED,
        status=EvidenceStatus.VERIFIED,
        confidence=0.95,
        evidence_refs=["model:guess"],
    )
    cause = make_cause(
        "the model thinks topic caused lift",
        relation=CausalRelation.SUPPORTED_CAUSE,
        supporting_evidence_refs=[model.evidence_id],
        confidence=0.9,
    )
    with pytest.raises(ValueError, match="learning-eligible"):
        build_evolution_diagnosis(
            agent_id="agent:ferro",
            created_at=NOW,
            experience_refs=["evx:diag"],
            evidence={model.evidence_id: model},
            evidence_refs=[model.evidence_id],
            observed_pattern="claimed lift",
            causes=[cause],
            status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
            confidence=0.3,
        )


def test_sufficient_diagnosis_requires_competing_causes_and_experiment():
    world = _world_evidence()
    cause = make_cause(
        "topic",
        relation=CausalRelation.SUSPECTED_CAUSE,
        supporting_evidence_refs=[world.evidence_id],
        confidence=0.4,
    )
    with pytest.raises(ValueError, match="competing causes"):
        build_evolution_diagnosis(
            agent_id="agent:ferro",
            created_at=NOW,
            experience_refs=["evx:diag"],
            evidence={world.evidence_id: world},
            evidence_refs=[world.evidence_id],
            observed_pattern="lift",
            causes=[cause],
            discriminating_experiment="vary topic",
            status=DiagnosisStatus.SUFFICIENT,
            confidence=0.5,
        )


def test_runtime_diagnosis_persists_and_binds_existing_evidence(tmp_path):
    rt = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    rt.step()
    experience = rt.evolution_experiences[0]
    world = rt.record_evolution_evidence(
        experience.experience_id,
        evidence_class=EvidenceClass.WORLD_OBSERVED,
        settled_at=NOW + timedelta(hours=2),
        metrics=[EvidenceMetric("pageviews", 100, "views")],
        evidence_refs=["analytics:1"],
    )
    causes = [
        make_cause(
            "headline quality",
            relation=CausalRelation.SUSPECTED_CAUSE,
            supporting_evidence_refs=[world.evidence_id],
            confidence=0.4,
        ),
        make_cause(
            "traffic timing",
            relation=CausalRelation.SUSPECTED_CAUSE,
            supporting_evidence_refs=[world.evidence_id],
            confidence=0.3,
        ),
    ]

    item = rt.record_evolution_diagnosis(
        experience_refs=[experience.experience_id],
        evidence_refs=[world.evidence_id],
        observed_pattern="pageviews increased",
        causes=causes,
        unknowns=["external referral spike"],
        discriminating_experiment="hold headline fixed across two time windows",
        applicability_scope="website-content",
        status=DiagnosisStatus.SUFFICIENT,
        confidence=0.5,
    )

    restored = AgentRuntime.restore(tmp_path, clock=FrozenClock(NOW))
    assert restored.evolution_diagnoses == [item]
