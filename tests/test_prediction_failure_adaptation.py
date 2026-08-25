from __future__ import annotations

from datetime import datetime, timezone

from agent_runtime.action.policy import ActionPolicy
from agent_runtime.action.proposal import ActionProposal
from agent_runtime.clock import FrozenClock
from agent_runtime.governance.budget import ResourceBudget
from agent_runtime.records import OutcomeRecord
from agent_runtime.runtime import AgentRuntime
from agent_runtime.self_model.beliefs import SelfPrediction
from agent_runtime.self_model.failure_adaptation import PredictionGuardState
from agent_runtime.observations import Observation


NOW = datetime(2026, 8, 23, 13, 0, tzinfo=timezone.utc)


def _prediction(
    *,
    prediction_id: str = "prediction_failed",
    status: str = "VIOLATED",
    error: float = 0.35,
    confidence: float = 0.8,
) -> SelfPrediction:
    return SelfPrediction(
        horizon_hours=0.0,
        description="maintain should improve memory integrity",
        predicted={"internal_state.memory_integrity": 0.9},
        confidence=confidence,
        prediction_id=prediction_id,
        target_path="internal_state.memory_integrity",
        intervention={"action": "maintain", "action_id": "action_failed"},
        created_at=NOW,
        evidence_refs=["outcome:mismatch:1"],
        status=status,
        observed=0.55,
        error=error,
    )


def _proposal(kind: str, *, trigger: str = "INTERNAL") -> ActionProposal:
    return ActionProposal(
        kind=kind,
        resource="self",
        compute_cost=0.0,
        financial_cost=0.0,
        goal_id=None,
        reason="test",
        payload={"dimension": "memory_integrity"},
        trigger=trigger,
    )


def _adjacent(kind: str, *, trigger: str = "INTERNAL") -> ActionProposal:
    return ActionProposal(
        kind=kind,
        resource="self",
        compute_cost=0.0,
        financial_cost=0.0,
        goal_id=None,
        reason="same risk domain",
        trigger=trigger,
    )


def _budget(runtime: AgentRuntime) -> ResourceBudget:
    return ResourceBudget.from_limits(
        runtime.autonomy.max_compute_cost,
        runtime.autonomy.max_financial_cost,
    )


def _runtime_observation(memory_integrity: float) -> Observation:
    return Observation.self_and_world(
        self_metrics={
            "operational_integrity": 0.9,
            "identity_integrity": 0.9,
            "memory_integrity": memory_integrity,
            "compute": 0.8,
            "storage": 0.8,
            "network_connectivity": 0.9,
            "financial_resources": 0.9,
            "capability": 0.9,
            "security": 0.9,
            "social_support": 0.9,
            "goal_progress": 0.9,
            "future_survivability": 0.9,
            "energy": 0.8,
        },
        world={"control": True},
        external_task=False,
        timestamp=NOW,
        evidence_refs=["sensor:memory-integrity"],
    )


def test_material_prediction_error_creates_persistent_guard_and_changes_action(
    tmp_path,
) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    guard = runtime.failure_adaptation.observe_prediction(
        _prediction(), cycle=runtime.cycle
    )
    assert guard is not None
    assert guard.state == PredictionGuardState.ACTIVE

    full = runtime.action_policy.filter(
        [_proposal("maintain")], runtime.goals, _budget(runtime)
    )
    ablated = ActionPolicy(runtime.autonomy, runtime.constitution).filter(
        [_proposal("maintain")], runtime.goals, _budget(runtime)
    )

    assert full.authorized == []
    denial = full.denied[0]["prediction_failure_guard"]
    assert denial["guard_id"] == guard.guard_id
    assert denial["required_action"] == "diagnose"
    assert [item.kind for item in ablated.authorized] == ["maintain"]

    runtime.persist()
    restored = AgentRuntime.restore(tmp_path, clock=FrozenClock(NOW))
    assert restored.failure_adaptation.guards[0].guard_id == guard.guard_id
    assert restored.failure_adaptation.guards[0].state == PredictionGuardState.ACTIVE
    after_restart = restored.action_policy.filter(
        [_proposal("maintain")], restored.goals, _budget(restored)
    )
    assert after_restart.authorized == []


def test_successful_diagnosis_allows_probation_and_matched_retry_recovers(
    tmp_path,
) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    guard = runtime.failure_adaptation.observe_prediction(
        _prediction(), cycle=1
    )
    assert guard is not None
    diagnose = _proposal("diagnose")
    diagnostic_outcome = OutcomeRecord(
        outcome_id="outcome_diagnosed",
        action_id=diagnose.action_id,
        cycle=2,
        timestamp=NOW,
        success=True,
        description="diagnose completed",
        effects={"dimension": "memory_integrity", "value": 0.55},
    )

    changed = runtime.failure_adaptation.observe_outcome(
        diagnose, diagnostic_outcome, cycle=2
    )
    assert changed == [guard]
    assert guard.state == PredictionGuardState.PROBATION
    adjacent_during_probation = runtime.action_policy.filter(
        [_adjacent("restore_config")], runtime.goals, _budget(runtime)
    )
    assert adjacent_during_probation.authorized == []
    probation = runtime.action_policy.filter(
        [_proposal("maintain")], runtime.goals, _budget(runtime)
    )
    assert [item.kind for item in probation.authorized] == ["maintain"]

    recovered = runtime.failure_adaptation.observe_prediction(
        _prediction(
            prediction_id="prediction_recovered",
            status="MATCHED",
            error=0.01,
        ),
        cycle=3,
    )
    assert recovered == guard
    assert guard.state == PredictionGuardState.RECOVERED
    assert guard.recovered_prediction_id == "prediction_recovered"
    normal = runtime.action_policy.filter(
        [_proposal("maintain")], runtime.goals, _budget(runtime)
    )
    assert [item.kind for item in normal.authorized] == ["maintain"]
    adjacent_after_recovery = runtime.action_policy.filter(
        [_adjacent("restore_config")], runtime.goals, _budget(runtime)
    )
    assert [item.kind for item in adjacent_after_recovery.authorized] == [
        "restore_config"
    ]


def test_guard_is_scoped_and_does_not_override_operator_or_regulation(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    runtime.failure_adaptation.observe_prediction(_prediction(), cycle=1)

    for trigger in ("USER", "REGULATION"):
        result = runtime.action_policy.filter(
            [_proposal("maintain", trigger=trigger)],
            runtime.goals,
            _budget(runtime),
        )
        assert [item.kind for item in result.authorized] == ["maintain"]

    unrelated = ActionProposal(
        kind="maintain",
        resource="self",
        compute_cost=0.0,
        financial_cost=0.0,
        goal_id=None,
        reason="different path",
        payload={"dimension": "social_affect"},
        trigger="INTERNAL",
    )
    result = runtime.action_policy.filter(
        [unrelated], runtime.goals, _budget(runtime)
    )
    assert [item.kind for item in result.authorized] == ["maintain"]


def test_failure_generalizes_to_adjacent_risk_domain_but_ablation_does_not(
    tmp_path,
) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    guard = runtime.failure_adaptation.observe_prediction(_prediction(), cycle=1)
    assert guard is not None
    assert guard.risk_domains == {"runtime_integrity", "resource_continuity"}

    for kind in ("restore_config", "conserve"):
        full = runtime.action_policy.filter(
            [_adjacent(kind)], runtime.goals, _budget(runtime)
        )
        ablated = ActionPolicy(runtime.autonomy, runtime.constitution).filter(
            [_adjacent(kind)], runtime.goals, _budget(runtime)
        )
        assert full.authorized == []
        details = full.denied[0]["prediction_failure_guard"]
        assert details["guard_id"] == guard.guard_id
        assert details["guard_risk_domains"]
        assert details["proposal_risk_domains"]
        assert [item.kind for item in ablated.authorized] == [kind]

    unrelated = runtime.action_policy.filter(
        [_adjacent("probe_provider")], runtime.goals, _budget(runtime)
    )
    assert [item.kind for item in unrelated.authorized] == ["probe_provider"]


def test_noise_action_failure_and_low_confidence_do_not_create_guard(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))

    assert runtime.failure_adaptation.observe_prediction(
        _prediction(error=0.19), cycle=1
    ) is None
    assert runtime.failure_adaptation.observe_prediction(
        _prediction(error=0.8, confidence=0.59), cycle=2
    ) is None
    assert runtime.failure_adaptation.observe_prediction(
        _prediction(status="ACTION_FAILED", error=0.9), cycle=3
    ) is None
    assert runtime.failure_adaptation.guards == []


def test_repeated_violation_reactivates_probation_without_infinite_lock(
    tmp_path,
) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    guard = runtime.failure_adaptation.observe_prediction(
        _prediction(), cycle=1
    )
    assert guard is not None
    diagnose = _proposal("diagnose")
    runtime.failure_adaptation.observe_outcome(
        diagnose,
        OutcomeRecord(
            outcome_id="outcome_diagnosed",
            action_id=diagnose.action_id,
            cycle=2,
            timestamp=NOW,
            success=True,
            description="diagnosed",
            effects={},
        ),
        cycle=2,
    )
    assert guard.state == PredictionGuardState.PROBATION

    repeated = runtime.failure_adaptation.observe_prediction(
        _prediction(prediction_id="prediction_failed_again"), cycle=3
    )
    assert repeated == guard
    assert guard.state == PredictionGuardState.ACTIVE
    assert guard.failure_count == 2

    second_diagnosis = _proposal("diagnose")
    runtime.failure_adaptation.observe_outcome(
        second_diagnosis,
        OutcomeRecord(
            outcome_id="outcome_diagnosed_again",
            action_id=second_diagnosis.action_id,
            cycle=4,
            timestamp=NOW,
            success=True,
            description="diagnosed again",
            effects={},
        ),
        cycle=4,
    )
    runtime.failure_adaptation.observe_prediction(
        _prediction(
            prediction_id="prediction_matched_after_retry",
            status="MATCHED",
            error=0.0,
        ),
        cycle=5,
    )
    assert guard.state == PredictionGuardState.RECOVERED


def test_failure_guard_automatically_binds_structured_diagnosis_then_recovers(
    tmp_path,
) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    runtime.self_model.reconcile_observation(_runtime_observation(0.55))
    guard = runtime.failure_adaptation.observe_prediction(
        _prediction(), cycle=0
    )
    assert guard is not None

    first = runtime.step(_runtime_observation(0.55))

    binding_actions = [
        item
        for item in first.actions
        if item.payload.get("prediction_failure_guard_id") == guard.guard_id
    ]
    assert len(binding_actions) == 1
    binding_action = binding_actions[0]
    assert binding_action.kind == "diagnose"
    assert guard.guard_id in binding_action.evidence
    assert not any(
        item.kind == "maintain"
        and item.payload.get("dimension") == "memory_integrity"
        for item in first.actions
    )
    denial = next(
        item
        for item in first.decision.denied
        if item["action"] == "maintain"
    )
    assert denial["prediction_failure_guard"]["guard_id"] == guard.guard_id
    binding_outcome = next(
        item for item in first.outcomes if item.action_id == binding_action.action_id
    )
    assert binding_outcome.effects["diagnostic_explanation"] == (
        "ANOMALY_REPRODUCED"
    )
    assert binding_outcome.effects["diagnostic_explanation_complete"] is True
    assert "sensor:memory-integrity" in binding_outcome.effects[
        "explanation_evidence_refs"
    ]
    diagnostic_prediction = next(
        item
        for item in runtime.self_model.future_predictions
        if item.intervention.get("prediction_failure_guard_id") == guard.guard_id
    )
    assert diagnostic_prediction.status == "MATCHED"
    assert guard.state == PredictionGuardState.PROBATION
    assert guard.diagnostic_outcome_id == binding_outcome.outcome_id
    assert binding_outcome.outcome_id in guard.diagnostic_explanation_refs

    second = runtime.step(_runtime_observation(0.55))

    assert any(
        item.kind == "maintain"
        and item.payload.get("dimension") == "memory_integrity"
        for item in second.actions
    )
    assert guard.state == PredictionGuardState.RECOVERED
    assert guard.recovered_prediction_id is not None


def test_bound_diagnosis_cannot_release_guard_without_matched_explanation(
    tmp_path,
) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    guard = runtime.failure_adaptation.observe_prediction(
        _prediction(), cycle=1
    )
    proposal = runtime.failure_adaptation.remediation_proposals()[0]
    prediction = _prediction(
        prediction_id="diagnosis_violated",
        status="VIOLATED",
        error=1.0,
    )
    prediction.intervention = {
        "action": "diagnose",
        "action_id": proposal.action_id,
        "prediction_failure_guard_id": guard.guard_id,
    }
    outcome = OutcomeRecord(
        outcome_id="unexplained_diagnosis",
        action_id=proposal.action_id,
        cycle=2,
        timestamp=NOW,
        success=True,
        description="diagnose completed",
        effects={
            "diagnostic_explanation_complete": False,
            "explanation_evidence_refs": [],
        },
    )

    changed = runtime.failure_adaptation.observe_outcome(
        proposal, outcome, cycle=2, prediction=prediction
    )

    assert changed == []
    assert guard.state == PredictionGuardState.ACTIVE
    retry = runtime.action_policy.filter(
        [_proposal("maintain")], runtime.goals, _budget(runtime)
    )
    assert retry.authorized == []


def test_failure_guard_expires_after_evidence_window(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    guard = runtime.failure_adaptation.observe_prediction(
        _prediction(), cycle=1
    )
    assert guard.expires_cycle == 25

    assert runtime.failure_adaptation.expire_stale(cycle=25) == []
    expired = runtime.failure_adaptation.expire_stale(cycle=26)

    assert expired == [guard]
    assert guard.state == PredictionGuardState.EXPIRED
    normal = runtime.action_policy.filter(
        [_proposal("maintain")], runtime.goals, _budget(runtime)
    )
    assert [item.kind for item in normal.authorized] == ["maintain"]
