from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_runtime.clock import FrozenClock
from agent_runtime.evolution import (
    EvidenceClass,
    EvidenceMetric,
    EvidenceStatus,
    EvolutionExperience,
    build_evolution_evidence,
)
from agent_runtime.runtime import AgentRuntime


NOW = datetime(2026, 9, 19, 14, 0, tzinfo=timezone.utc)


def _experience() -> EvolutionExperience:
    return EvolutionExperience(
        experience_id="evx:test",
        trace_id="trace:test",
        agent_id="agent:ferro",
        logical_cycle=7,
        observed_at=NOW,
        effective_at=NOW,
        sealed_at=NOW,
        goal_refs=("goal:1",),
        plan_ref="plan:1",
        step_ref="step:1",
        decision_ref="decision:1",
        action_refs=("action:1",),
        outcome_refs=(),
        caused_by=("basis:1",),
        evidence_refs=("basis:1",),
        context_refs=("obs:1",),
        belief_refs=("belief:1",),
        version_bindings=(),
        compute_cost=2.0,
        financial_cost=0.2,
        outcome_known_at=None,
        confidence=0.9,
    )


def test_world_evidence_is_learning_eligible_and_retains_dimensions():
    item = build_evolution_evidence(
        _experience(),
        settled_at=NOW + timedelta(hours=4),
        evidence_class=EvidenceClass.WORLD_OBSERVED,
        status=EvidenceStatus.VERIFIED,
        outcome_success=True,
        process_score=0.8,
        quality_score=0.7,
        metrics=[
            EvidenceMetric("followers_delta", 12, "followers"),
            EvidenceMetric("profile_visits", 44, "visits"),
        ],
        side_effects=["reply-volume-increased"],
        confidence=0.85,
        evidence_refs=["x:post:1", "x:snapshot:2"],
    )

    assert item.learning_eligible is True
    assert item.compute_cost == 2.0
    assert item.financial_cost == 0.2
    assert item.process_score == 0.8
    assert item.quality_score == 0.7
    assert [metric.name for metric in item.metrics] == [
        "followers_delta",
        "profile_visits",
    ]
    assert item.side_effects == ("reply-volume-increased",)


def test_model_inference_cannot_impersonate_world_evidence():
    item = build_evolution_evidence(
        _experience(),
        settled_at=NOW + timedelta(minutes=5),
        evidence_class=EvidenceClass.MODEL_INFERRED,
        status=EvidenceStatus.VERIFIED,
        outcome_success=True,
        confidence=0.99,
        evidence_refs=["model:reflection:1"],
    )
    assert item.status is EvidenceStatus.VERIFIED
    assert item.learning_eligible is False


def test_unknown_cannot_be_verified_and_missing_units_stay_explicit():
    with pytest.raises(ValueError, match="known evidence class"):
        build_evolution_evidence(
            _experience(),
            settled_at=NOW,
            evidence_class=EvidenceClass.UNKNOWN,
            status=EvidenceStatus.VERIFIED,
        )

    item = build_evolution_evidence(
        _experience(),
        settled_at=NOW + timedelta(hours=1),
        evidence_class=EvidenceClass.UNKNOWN,
        status=EvidenceStatus.INSUFFICIENT_EVIDENCE,
        missing_evidence_units=["external-outcome"],
        confidence=0.2,
    )
    assert item.learning_eligible is False
    assert item.missing_evidence_units == ("external-outcome",)


def test_evidence_cannot_bind_action_from_another_experience():
    with pytest.raises(ValueError, match="does not belong"):
        build_evolution_evidence(
            _experience(),
            settled_at=NOW,
            evidence_class=EvidenceClass.SYSTEM_MEASURED,
            status=EvidenceStatus.VERIFIED,
            action_refs=["action:other"],
        )


def test_runtime_cycle_creates_and_restores_canonical_evidence(tmp_path):
    rt = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))

    result = rt.step()

    assert len(rt.evolution_experiences) == 1
    assert len(rt.evolution_evidence) == len(result.outcomes)
    assert all(
        item.evidence_class is EvidenceClass.SYSTEM_MEASURED
        for item in rt.evolution_evidence
    )
    assert all(item.experience_id == rt.evolution_experiences[0].experience_id for item in rt.evolution_evidence)

    restored = AgentRuntime.restore(tmp_path, clock=FrozenClock(NOW))
    assert restored.evolution_evidence == rt.evolution_evidence


def test_runtime_accepts_delayed_domain_evidence_without_rewriting_experience(tmp_path):
    rt = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    rt.step()
    experience = rt.evolution_experiences[0]
    sealed_before = experience

    item = rt.record_evolution_evidence(
        experience.experience_id,
        evidence_class=EvidenceClass.WORLD_OBSERVED,
        settled_at=NOW + timedelta(hours=24),
        outcome_success=True,
        quality_score=0.9,
        metrics=[EvidenceMetric("pageviews", 120, "views")],
        evidence_refs=["website:analytics:day1"],
    )

    assert item.learning_eligible is True
    assert rt.evolution_experiences[0] == sealed_before
    assert len(rt.evolution_evidence) >= 2
    restored = AgentRuntime.restore(tmp_path, clock=FrozenClock(NOW))
    assert item in restored.evolution_evidence
