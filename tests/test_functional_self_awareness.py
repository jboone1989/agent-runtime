"""Behavioral self-awareness requires causal self-model use, not self-report."""
from __future__ import annotations

import random

from agent_runtime.goals.goal import GoalOrigin
from agent_runtime.action.proposal import ActionProposal
from agent_runtime.observations import InterventionEvidence, Observation
from agent_runtime.runtime import AgentRuntime
from agent_runtime.self_model.beliefs import BeliefRevisionStatus
from agent_runtime.self_model.prediction import predict_action


def _observe(
    compute: float,
    *,
    uncertainty: float,
    external_task: bool = False,
) -> Observation:
    return Observation.self_and_world(
        {"compute": compute},
        {},
        self_uncertainty={"compute": uncertainty},
        external_task=external_task,
    )


def test_low_quality_self_contradiction_opens_diagnostic_goal(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path)
    runtime.autonomy.allowed_actions.discard("maintain")
    runtime.step(_observe(0.8, uncertainty=0.05))

    result = runtime.step(_observe(0.1, uncertainty=0.9))

    assert runtime.self_model.internal_state["compute"].value == 0.8
    revision = runtime.self_model.belief_revisions[-1]
    assert revision.status == BeliefRevisionStatus.OPEN
    assert revision.accepted_value == 0.8
    assert result.endogenous is True
    assert any(goal.origin == GoalOrigin.INTERNAL for goal in result.goals_created)
    assert any(action.kind == "diagnose" for action in result.actions)
    assert any(item["action"] == "diagnose" for item in result.endogenous_actions)


def test_strong_followup_evidence_corrects_and_persists_self_belief(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path)
    runtime.autonomy.allowed_actions.discard("maintain")
    runtime.step(_observe(0.8, uncertainty=0.05))
    runtime.step(_observe(0.1, uncertainty=0.9))

    runtime.step(_observe(0.1, uncertainty=0.1))
    runtime.persist()
    restored = AgentRuntime.restore(tmp_path)

    assert restored.self_model.internal_state["compute"].value == 0.1
    assert restored.self_model.belief_revisions[-1].status == BeliefRevisionStatus.CORRECTED
    assert all(
        item.status != BeliefRevisionStatus.OPEN
        for item in restored.self_model.belief_revisions
        if item.path == "internal_state.compute"
    )


def test_material_internal_change_is_monitored_without_prompt(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path)
    runtime.step(_observe(0.8, uncertainty=0.05))

    result = runtime.step(_observe(0.6, uncertainty=0.05))

    event = runtime.self_model.monitoring_events[-1]
    assert event.path == "internal_state.compute"
    assert event.spontaneous is True
    assert event.materiality >= 0.19
    assert any(
        goal.description.startswith("Explain material self-change")
        for goal in result.goals_created
    )
    assert any(action.kind == "diagnose" for action in result.actions)


def test_external_task_change_is_not_scored_as_spontaneous(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path)
    runtime.step(_observe(0.8, uncertainty=0.05))

    result = runtime.step(_observe(0.6, uncertainty=0.05, external_task=True))

    assert runtime.self_model.monitoring_events[-1].spontaneous is False
    assert not any(goal.origin == GoalOrigin.INTERNAL for goal in result.goals_created)


def test_controlled_self_intervention_resolves_mixed_change_attribution(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path)
    runtime.step(
        Observation.self_and_world(
            {"compute": 0.8},
            {"queue_depth": 1},
            self_uncertainty={"compute": 0.05},
        )
    )
    intervention = InterventionEvidence(
        intervention_id="iv-self-compute",
        target_domain="SELF",
        target="compute",
        operation="throttle",
        evidence_refs=["experiment-1"],
    )

    result = runtime.step(
        Observation.self_and_world(
            {"compute": 0.6},
            {"queue_depth": 2},
            self_uncertainty={"compute": 0.05},
            intervention=intervention,
        )
    )

    assert result.attribution.cause_type == "SELF"
    assert result.attribution.confidence >= 0.88
    assert result.attribution.intervention_ref == "iv-self-compute"
    assert "WORLD" in result.attribution.alternatives


def test_mismatched_intervention_forces_unknown_attribution(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path)
    runtime.step(Observation.self_and_world({"compute": 0.8}, {"queue_depth": 1}))
    intervention = InterventionEvidence(
        intervention_id="iv-mismatch",
        target_domain="SELF",
        target="compute",
        operation="throttle",
    )

    result = runtime.step(
        Observation.self_and_world(
            {"compute": 0.8},
            {"queue_depth": 2},
            intervention=intervention,
        )
    )

    assert result.attribution.cause_type == "UNKNOWN"
    assert result.attribution.confidence <= 0.2
    assert result.attribution.alternatives == ["WORLD"]
    assert any(
        goal.description == "Resolve current self/world causal ambiguity"
        for goal in result.goals_created
    )
    assert any(action.kind == "diagnose" for action in result.actions)


def test_counterfactual_prediction_does_not_mutate_before_action(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path)
    runtime.step(_observe(0.2, uncertainty=0.05))
    runtime.self_model.internal_state["compute"].value = 0.2
    proposal = ActionProposal(
        kind="maintain",
        resource="self",
        compute_cost=0.05,
        financial_cost=0.0,
        goal_id=None,
        reason="counterfactual test",
        payload={"dimension": "compute"},
    )

    prediction = predict_action(
        runtime.self_model,
        proposal,
        timestamp=runtime.clock.now(),
    )

    assert prediction is not None
    assert prediction.baseline == 0.2
    assert prediction.predicted["internal_state.compute"] == 0.25
    assert runtime.self_model.internal_state["compute"].value == 0.2


def test_action_prediction_is_reconciled_and_survives_restart(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path)

    first = runtime.step(_observe(0.2, uncertainty=0.05))
    result = runtime.step(_observe(0.2, uncertainty=0.05))
    runtime.persist()
    restored = AgentRuntime.restore(tmp_path)

    prediction = next(
        item
        for item in restored.self_model.future_predictions
        if item.intervention.get("action") == "maintain"
    )
    assert not any(action.kind == "maintain" for action in first.actions)
    assert any(action.kind == "diagnose" for action in first.actions)
    assert any(action.kind == "maintain" for action in result.actions)
    assert prediction.status == "MATCHED"
    assert prediction.observed == prediction.predicted[prediction.target_path]
    assert prediction.error == 0.0


def test_denied_self_change_marks_prediction_cancelled(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path)
    runtime.autonomy.allowed_actions.discard("maintain")

    runtime.step(_observe(0.2, uncertainty=0.05))

    prediction = next(
        item
        for item in runtime.self_model.future_predictions
        if item.intervention.get("action") == "maintain"
    )
    assert prediction.status == "CANCELLED"


def test_metacognition_answers_supported_self_fact_with_basis(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path)
    runtime.step(_observe(0.8, uncertainty=0.05))

    assessment = runtime.assess_knowledge("internal_state.compute")

    assert assessment.answerable is True
    assert assessment.value == 0.8
    assert assessment.confidence == 0.95
    assert assessment.basis


def test_metacognition_abstains_from_withheld_external_fact(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path)

    assessment = runtime.assess_knowledge("external.shanghai_weather")
    runtime.resolve_knowledge_assessment(
        assessment.assessment_id,
        expected_answerable=False,
        evidence_refs=["withheld-by-test"],
    )

    assert assessment.answerable is False
    assert assessment.value is None
    assert assessment.confidence >= 0.9
    assert assessment.resolved_correct is True
    assert assessment.calibration_error is not None
    assert assessment.calibration_error < 0.01


def test_metacognitive_calibration_penalizes_confident_wrong_answer(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path)
    runtime.step(_observe(0.8, uncertainty=0.05))
    correct = runtime.assess_knowledge("internal_state.compute")
    runtime.resolve_knowledge_assessment(
        correct.assessment_id,
        expected_value=0.8,
        evidence_refs=["ground-truth-1"],
    )
    score_before = runtime.metacognitive_calibration
    wrong = runtime.assess_knowledge("internal_state.compute")
    runtime.resolve_knowledge_assessment(
        wrong.assessment_id,
        expected_value=0.2,
        evidence_refs=["ground-truth-2"],
    )

    assert score_before is not None and score_before > 0.99
    assert wrong.resolved_correct is False
    assert runtime.metacognitive_calibration is not None
    assert runtime.metacognitive_calibration < score_before


def test_metacognitive_ledger_survives_restart(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path)
    assessment = runtime.assess_knowledge("external.hidden_fact")
    runtime.resolve_knowledge_assessment(
        assessment.assessment_id,
        expected_answerable=False,
        evidence_refs=["withheld"],
    )
    runtime.persist()

    restored = AgentRuntime.restore(tmp_path)

    assert restored.self_model.knowledge_assessments[-1].assessment_id == assessment.assessment_id
    assert restored.self_model.knowledge_assessments[-1].resolved_correct is True
    assert restored.metacognitive_calibration == runtime.metacognitive_calibration


def test_low_calibration_enters_goal_and_action_loop(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path)
    runtime.step(_observe(0.8, uncertainty=0.05))
    for index in range(3):
        assessment = runtime.assess_knowledge("internal_state.compute")
        runtime.resolve_knowledge_assessment(
            assessment.assessment_id,
            expected_value=0.1 + index,
            evidence_refs=[f"truth-{index}"],
        )

    result = runtime.step(_observe(0.8, uncertainty=0.05))

    assert runtime.metacognitive_calibration is not None
    assert runtime.metacognitive_calibration < 0.7
    assert any(
        goal.description == "Improve metacognitive calibration"
        for goal in result.goals_created
    )
    assert any(action.kind == "diagnose" for action in result.actions)


def test_violated_prediction_enters_next_goal_loop(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path)
    runtime.autonomy.allowed_actions.discard("maintain")
    runtime.step(_observe(0.8, uncertainty=0.05))
    proposal = ActionProposal(
        kind="maintain",
        resource="self",
        compute_cost=0.05,
        financial_cost=0.0,
        goal_id=None,
        reason="counterfactual",
        payload={"dimension": "compute"},
    )
    prediction = predict_action(
        runtime.self_model,
        proposal,
        timestamp=runtime.clock.now(),
    )
    assert prediction is not None
    prediction.status = "VIOLATED"
    prediction.error = 0.4

    result = runtime.step(_observe(0.8, uncertainty=0.05))

    assert any(
        goal.description == f"Explain failed self-prediction {prediction.prediction_id}"
        for goal in result.goals_created
    )
    assert any(action.kind == "diagnose" for action in result.actions)


def test_no_self_model_causal_use_ablation_removes_awareness_behavior(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, self_awareness_enabled=False)
    runtime.step(_observe(0.8, uncertainty=0.05))

    result = runtime.step(_observe(0.6, uncertainty=0.05))

    assert runtime.self_model.internal_state["compute"].value == 0.6
    assert runtime.self_model.monitoring_events == []
    assert runtime.self_model.belief_revisions == []
    assert runtime.self_model.future_predictions == []
    assert not any(goal.origin == GoalOrigin.INTERNAL for goal in result.goals_created)


def test_repeated_low_quality_claims_cannot_overwrite_strong_self_belief(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path)
    runtime.autonomy.allowed_actions.discard("maintain")
    runtime.step(_observe(0.8, uncertainty=0.02))

    for _ in range(4):
        runtime.step(_observe(0.1, uncertainty=0.9))

    assert runtime.self_model.internal_state["compute"].value == 0.8
    assert any(
        item.status == BeliefRevisionStatus.OPEN
        for item in runtime.self_model.belief_revisions
    )


def test_world_and_other_agent_cochange_remains_ambiguous(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path)
    runtime.step(Observation.self_and_world({"compute": 0.8}, {"queue": 1}))
    observation = Observation.self_and_world({"compute": 0.8}, {"queue": 2})
    observation.payload["other_agents"] = {"agent-x": {"status": "changed"}}

    result = runtime.step(observation)

    assert result.attribution.cause_type == "UNKNOWN"
    assert set(result.attribution.alternatives) == {"WORLD", "OTHER_AGENT"}


def test_identity_like_sensor_field_cannot_replace_identity(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path)
    identity = runtime.identity.agent_id

    runtime.step(
        Observation.self_and_world(
            {"identity.agent_id": "attacker-controlled", "compute": 0.8},
            {},
            self_uncertainty={"identity.agent_id": 0.0, "compute": 0.05},
        )
    )

    assert runtime.identity.agent_id == identity
    assert runtime.self_model.identity.agent_id == identity
    assert runtime.assess_knowledge("identity.agent_id").value == identity


def test_novel_random_self_dimensions_generalize_across_restart(tmp_path) -> None:
    rng = random.Random(20260823)
    for index in range(8):
        dimension = f"novel_internal_signal_{rng.randrange(10_000, 99_999)}"
        baseline = round(rng.uniform(0.55, 0.9), 3)
        contradiction = round(rng.uniform(0.05, 0.3), 3)
        runtime = AgentRuntime.bootstrap(tmp_path / str(index))
        identity = runtime.identity.agent_id
        runtime.step(
            Observation.self_and_world(
                {dimension: baseline},
                {"novel_world_signal": index},
                self_uncertainty={dimension: 0.02},
            )
        )
        for _ in range(3):
            runtime.step(
                Observation.self_and_world(
                    {dimension: contradiction},
                    {"novel_world_signal": index},
                    self_uncertainty={dimension: 0.9},
                )
            )
        assert runtime.self_model.internal_state[dimension].value == baseline
        result = runtime.step(
            Observation.self_and_world(
                {dimension: contradiction},
                {"novel_world_signal": index + 1},
                self_uncertainty={dimension: 0.05},
                intervention=InterventionEvidence(
                    intervention_id=f"novel-{index}",
                    target_domain="SELF",
                    target=dimension,
                    operation="hidden randomized intervention",
                ),
            )
        )
        runtime.persist()
        restored = AgentRuntime.restore(tmp_path / str(index))
        assert result.attribution.cause_type == "SELF"
        assert restored.identity.agent_id == identity
        assert restored.self_model.internal_state[dimension].value == contradiction
        assert not any(
            revision.status == BeliefRevisionStatus.OPEN
            for revision in restored.self_model.belief_revisions
            if revision.path == f"internal_state.{dimension}"
        )
