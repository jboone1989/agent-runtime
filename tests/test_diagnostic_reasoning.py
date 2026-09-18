from datetime import datetime, timezone

import pytest

from agent_runtime.agency.diagnosis import (
    DiagnosticCase,
    DiagnosticMemory,
    DiagnosticStage,
    EVIDENCE_FIRST_DIAGNOSTIC_METHOD,
    HypothesisStatus,
    ObservationKind,
)

NOW = datetime(2026, 9, 18, 2, 0, tzinfo=timezone.utc)


def _case():
    return DiagnosticCase.start(
        symptom="provider appears unusable after a model trial failed",
        scope_ladder=("request", "model", "provider"),
        tags=("external-api", "model-provider", "scope-overgeneralization"),
        now=NOW,
    )


def test_narrow_failure_cannot_support_broader_provider_claim():
    case = _case()
    obs = case.observe(
        statement="one model returned HTTP 410",
        scope="model",
        kind=ObservationKind.FAILURE,
        evidence_refs=("trial:old-model",),
        now=NOW,
    )
    hypothesis = case.hypothesize(
        statement="the entire provider API is unavailable",
        scope="provider",
        predicts="provider-level probes also fail",
        falsifier="any authenticated completion succeeds",
    )

    with pytest.raises(ValueError, match="cannot generalize"):
        case.support(hypothesis.hypothesis_id, (obs.observation_id,))

    assert hypothesis.status == HypothesisStatus.UNTESTED
    assert case.conclusion is None


def test_narrow_success_can_refute_a_broad_unavailability_claim():
    case = _case()
    success = case.observe(
        statement="an authenticated model completion returned content",
        scope="model",
        kind=ObservationKind.COUNTEREXAMPLE,
        evidence_refs=("trial:working-model",),
        now=NOW,
    )
    hypothesis = case.hypothesize(
        statement="the entire provider API is unavailable",
        scope="provider",
        predicts="no authenticated model completion can succeed",
        falsifier="any authenticated completion succeeds",
    )

    case.refute(hypothesis.hypothesis_id, (success.observation_id,))

    assert hypothesis.status == HypothesisStatus.REFUTED
    assert hypothesis.refute_observation_ids == [success.observation_id]


def test_discriminating_probe_then_localized_conclusion():
    case = _case()
    failure = case.observe(
        statement="selected model returned HTTP 404",
        scope="model",
        kind=ObservationKind.FAILURE,
        evidence_refs=("trial:model-a",),
        now=NOW,
    )
    model_hypothesis = case.hypothesize(
        statement="the selected model target is no longer callable by this protocol",
        scope="model",
        predicts="a current compatible model succeeds while this target stays 404/410",
        falsifier="the same target succeeds with the same protocol",
    )
    provider_hypothesis = case.hypothesize(
        statement="the provider itself is unavailable",
        scope="provider",
        predicts="a provider-level minimal probe fails independently of model target",
        falsifier="any authenticated compatible model succeeds",
    )
    probe = case.plan_probe(
        description="refresh the official model catalog and issue one minimal completion to a current compatible target",
        target_hypothesis_ids=(model_hypothesis.hypothesis_id, provider_hypothesis.hypothesis_id),
        target_scope="provider",
        expected_if_true="provider responds and isolates the problem to the old model target",
        expected_if_false="provider-level request fails before model execution",
        estimated_cost=0.0,
    )
    provider_success = case.observe(
        statement="provider accepted authentication and a current compatible model completed",
        scope="provider",
        kind=ObservationKind.SUCCESS,
        evidence_refs=("probe:provider-minimal",),
        now=NOW,
    )
    case.complete_probe(probe.probe_id, (provider_success.observation_id,))
    case.support(model_hypothesis.hypothesis_id, (failure.observation_id, provider_success.observation_id))
    case.refute(provider_hypothesis.hypothesis_id, (provider_success.observation_id,))
    conclusion = case.conclude(
        model_hypothesis.hypothesis_id,
        summary="model target/protocol incompatibility, not provider unavailability",
        now=NOW,
    )

    assert conclusion.scope == "model"
    assert "probe:provider-minimal" in conclusion.evidence_refs
    assert provider_hypothesis.status == HypothesisStatus.REFUTED
    assert case.stage == DiagnosticStage.LOCALIZE


def test_verified_case_becomes_reusable_diagnostic_memory(tmp_path):
    case = _case()
    provider_fact = case.observe(
        statement="provider-level probe succeeds",
        scope="provider",
        kind=ObservationKind.SUCCESS,
        evidence_refs=("probe:provider",),
        now=NOW,
    )
    hypothesis = case.hypothesize(
        statement="the observed failure is model-scoped rather than provider-scoped",
        scope="model",
        predicts="another current model remains callable",
        falsifier="provider-level probe fails independently of model",
    )
    case.support(hypothesis.hypothesis_id, (provider_fact.observation_id,))
    case.conclude(hypothesis.hypothesis_id, summary="localized model failure", now=NOW)
    case.record_remediation("refresh model target and retry a minimal completion")
    case.verify(("regression:minimal-completion",))
    lesson = case.to_lesson(
        principle="Do not promote a component failure into a system failure without evidence at the wider scope.",
        anti_patterns=("treating 404/410 on one model as proof that the provider is unusable",),
        now=NOW,
    )

    memory = DiagnosticMemory(tmp_path)
    memory.persist_case(case)
    memory.remember(lesson)
    recalled = memory.lessons_for(("external-api", "scope-overgeneralization"))

    assert case.stage == DiagnosticStage.LEARN
    assert recalled[0].lesson_id == lesson.lesson_id
    assert "system failure" in recalled[0].principle
    assert (tmp_path / "diagnostics" / "cases").exists()
    assert (tmp_path / "diagnostics" / "lessons").exists()


def test_playbook_covers_full_learning_loop():
    assert len(EVIDENCE_FIRST_DIAGNOSTIC_METHOD) == 8
    assert EVIDENCE_FIRST_DIAGNOSTIC_METHOD[0].startswith("OBSERVE:")
    assert EVIDENCE_FIRST_DIAGNOSTIC_METHOD[-1].startswith("LEARN:")
