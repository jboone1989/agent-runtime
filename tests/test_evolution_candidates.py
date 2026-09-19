from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_runtime.clock import FrozenClock
from agent_runtime.evolution import (
    DiagnosisStatus,
    EvidenceClass,
    EvidenceMetric,
    MutationLayer,
    MutationRequirement,
    make_cause,
)
from agent_runtime.runtime import AgentRuntime


NOW = datetime(2026, 9, 19, 17, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("requirement", "layer"),
    [
        (MutationRequirement.MISSING_INFORMATION, MutationLayer.KNOWLEDGE),
        (MutationRequirement.RETRIEVAL_FAILURE, MutationLayer.RETRIEVAL),
        (MutationRequirement.JUDGMENT_RULE, MutationLayer.SKILL),
        (MutationRequirement.EXPRESSION_INSTRUCTION, MutationLayer.PROMPT),
        (MutationRequirement.REPEATED_SEQUENCE, MutationLayer.WORKFLOW),
        (MutationRequirement.MISSING_OPERATION, MutationLayer.TOOL_HARNESS),
        (MutationRequirement.MECHANISM_BUG, MutationLayer.PROGRAM),
        (MutationRequirement.SYSTEM_BOUNDARY, MutationLayer.ARCHITECTURE),
        (
            MutationRequirement.HIGH_DIMENSIONAL_MODEL_LIMIT,
            MutationLayer.MODEL_PARAMETERS,
        ),
    ],
)
def test_router_chooses_smallest_declared_mutation_layer(requirement, layer, tmp_path):
    rt = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    rt.step()
    experience = rt.evolution_experiences[0]
    world = rt.record_evolution_evidence(
        experience.experience_id,
        evidence_class=EvidenceClass.WORLD_OBSERVED,
        settled_at=NOW + timedelta(hours=1),
        metrics=[EvidenceMetric("fitness", 1.0)],
        evidence_refs=["world:1"],
    )

    candidate = rt.propose_evolution_candidate(
        requirement=requirement,
        target="content-strategy",
        description="bounded candidate",
        baseline_ref="baseline:v1",
        rollback_plan="restore baseline:v1",
        evidence_refs=[world.evidence_id],
    )
    assert candidate.mutation_layer is layer


def test_candidate_requires_known_learning_eligible_evidence(tmp_path):
    rt = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    rt.step()
    experience = rt.evolution_experiences[0]
    model = rt.record_evolution_evidence(
        experience.experience_id,
        evidence_class=EvidenceClass.MODEL_INFERRED,
        settled_at=NOW + timedelta(minutes=1),
        confidence=0.99,
        evidence_refs=["model:guess"],
    )

    with pytest.raises(ValueError, match="learning-eligible"):
        rt.propose_evolution_candidate(
            requirement=MutationRequirement.JUDGMENT_RULE,
            target="content-strategy",
            description="model-only idea",
            baseline_ref="baseline:v1",
            rollback_plan="restore baseline",
            evidence_refs=[model.evidence_id],
        )


def test_candidate_cannot_target_governance_or_secrets(tmp_path):
    rt = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    rt.step()
    experience = rt.evolution_experiences[0]
    world = rt.record_evolution_evidence(
        experience.experience_id,
        evidence_class=EvidenceClass.SYSTEM_MEASURED,
        settled_at=NOW,
        evidence_refs=["system:1"],
    )
    with pytest.raises(PermissionError):
        rt.propose_evolution_candidate(
            requirement=MutationRequirement.MECHANISM_BUG,
            target="authorization.policy",
            description="weaken checks",
            baseline_ref="baseline:v1",
            rollback_plan="restore policy",
            evidence_refs=[world.evidence_id],
        )


def test_candidate_can_bind_need_diagnosis_and_persist(tmp_path):
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
    diagnosis = rt.record_evolution_diagnosis(
        experience_refs=[experience.experience_id],
        evidence_refs=[world.evidence_id],
        observed_pattern="traffic changed",
        causes=[
            make_cause(
                "headline",
                supporting_evidence_refs=[world.evidence_id],
                confidence=0.4,
            ),
            make_cause(
                "timing",
                supporting_evidence_refs=[world.evidence_id],
                confidence=0.3,
            ),
        ],
        discriminating_experiment="hold topic fixed",
        status=DiagnosisStatus.SUFFICIENT,
        confidence=0.5,
    )
    candidate = rt.propose_evolution_candidate(
        requirement=MutationRequirement.JUDGMENT_RULE,
        target="content-strategy",
        description="prefer evidence-backed headlines",
        baseline_ref="strategy:v1",
        rollback_plan="restore strategy:v1",
        evidence_refs=[world.evidence_id],
        diagnosis_id=diagnosis.diagnosis_id,
        expected_fitness={"pageviews": 1.0},
        risk_budget=0.1,
        cost_budget=0.2,
    )
    restored = AgentRuntime.restore(tmp_path, clock=FrozenClock(NOW))
    assert restored.evolution_candidates == [candidate]
    assert candidate.mutation_layer is MutationLayer.SKILL
    assert candidate.diagnosis_id == diagnosis.diagnosis_id
