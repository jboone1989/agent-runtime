from __future__ import annotations

from datetime import datetime, timezone

from agent_runtime.agency.experiment import (
    AgencyExperiment,
    AgencyExperimentStatus,
    ExperimentFailureBindingStatus,
    ExperimentFailureKind,
)
from agent_runtime.action.proposal import ActionProposal
from agent_runtime.clock import FrozenClock
from agent_runtime.observations import Observation
from agent_runtime.records import OutcomeRecord
from agent_runtime.runtime import AgentRuntime
from agent_runtime.self_model.beliefs import SelfPrediction


NOW = datetime(2026, 8, 23, 16, 0, tzinfo=timezone.utc)


def _schedule(runtime: AgentRuntime, *, cycle: int = 1) -> AgencyExperiment:
    return runtime.agency_experiment_manager.schedule(
        AgencyExperiment(
            experiment_id="apexp_maintain_memory_integrity",
            thread_id="ap_continuity_vs_authorization",
            statement="maintain should keep memory_integrity at 1.0",
            action_kind="maintain",
            resource="self",
            payload={"dimension": "memory_integrity"},
            scheduled_cycle=cycle,
            status=AgencyExperimentStatus.PLANNED,
            evidence_refs=["mailbox:agent-selected-experiment"],
        )
    )


def _observation() -> Observation:
    return Observation.self_and_world(
        self_metrics={"compute": 0.8, "memory_integrity": 1.0},
        world={"unrelated": "control"},
        external_task=True,
        timestamp=NOW,
    )


def test_agent_selected_experiment_runs_once_through_normal_loop(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    experiment = _schedule(runtime)

    result = runtime.step(_observation())

    assert experiment.status == AgencyExperimentStatus.COMPLETED
    assert experiment.action_id is not None
    assert experiment.outcome_id is not None
    assert experiment.prediction_id is not None
    assert "prediction_status=MATCHED" in experiment.result_summary
    assert any(
        action.action_id == experiment.action_id
        and experiment.experiment_id in action.evidence
        and action.trigger == "INTERNAL"
        for action in result.actions
    )
    assert runtime.llm_provider is None

    runtime.step(_observation())
    matching = [
        action
        for action in runtime.records.actions
        if experiment.experiment_id in action.evidence
    ]
    assert len(matching) == 1


def test_experiment_cannot_bypass_action_policy(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    runtime.autonomy.allowed_actions.discard("maintain")
    experiment = _schedule(runtime)

    result = runtime.step(_observation())

    assert experiment.status == AgencyExperimentStatus.DENIED
    assert experiment.outcome_id is None
    assert "action not allowed" in experiment.denial_reason
    assert not any(
        experiment.experiment_id in action.evidence for action in result.actions
    )
    assert experiment.failure_binding_id is not None
    assert experiment.denial_details["category"] == "AUTHORIZATION"
    assert experiment.denial_details["authority_ref"] == "autonomy.allowed_actions"
    binding = runtime.agency_experiment_manager.failure_bindings[0]
    assert binding.failure_kind == ExperimentFailureKind.DENIED
    assert binding.outcome_id is None
    assert binding.prediction_id is None
    assert "policy:autonomy.allowed_actions" in binding.failure_evidence_refs
    assert "maintain" not in runtime.autonomy.allowed_actions


def test_planned_experiment_survives_restart_then_executes(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    experiment = _schedule(runtime, cycle=2)
    runtime.step(_observation())
    runtime.persist()

    restored = AgentRuntime.restore(tmp_path, clock=FrozenClock(NOW))
    restored_experiment = restored.agency_experiment_manager.experiment(
        experiment.experiment_id
    )
    assert restored_experiment.status == AgencyExperimentStatus.PLANNED
    restored.step(_observation())
    assert restored_experiment.status == AgencyExperimentStatus.COMPLETED


def test_experiment_ablation_preserves_state_but_removes_action_effect(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    experiment = _schedule(runtime)
    runtime.persist()

    ablated = AgentRuntime.restore(
        tmp_path,
        clock=FrozenClock(NOW),
        agency_experiments_enabled=False,
    )
    result = ablated.step(_observation())
    preserved = ablated.agency_experiment_manager.experiment(
        experiment.experiment_id
    )

    assert preserved.status == AgencyExperimentStatus.PLANNED
    assert not any(
        experiment.experiment_id in action.evidence for action in result.actions
    )


def test_diagnose_experiment_matches_real_structured_numeric_reading(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    experiment = runtime.agency_experiment_manager.schedule(
        AgencyExperiment(
            experiment_id="apexp_diagnose_llm_error_rate",
            thread_id="ap_continuity_vs_authorization",
            statement="llm_error_rate should be within [0.0, 0.1]",
            action_kind="diagnose",
            resource="self",
            payload={
                "dimension": "llm_error_rate",
                "expected_range": [0.0, 0.1],
            },
            scheduled_cycle=1,
            status=AgencyExperimentStatus.PLANNED,
            evidence_refs=["mailbox:agent-selected-diagnosis"],
        )
    )
    observation = Observation.self_and_world(
        self_metrics={"compute": 0.8, "llm_error_rate": 0.02},
        world={},
        timestamp=NOW,
        evidence_refs=["sensor:llm-error-rate"],
        self_uncertainty={"llm_error_rate": 0.33},
    )

    result = runtime.step(observation)

    assert experiment.status == AgencyExperimentStatus.COMPLETED
    assert "prediction_status=MATCHED" in experiment.result_summary
    outcome = next(item for item in result.outcomes if item.outcome_id == experiment.outcome_id)
    assert outcome.effects["target_path"] == "internal_state.llm_error_rate"
    assert outcome.effects["value"] == 0.02
    assert outcome.effects["uncertainty"] == 0.33
    assert "sensor:llm-error-rate" in outcome.effects["evidence_refs"]


def test_required_prediction_missing_is_experiment_failure(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    experiment = runtime.agency_experiment_manager.schedule(
        AgencyExperiment(
            experiment_id="apexp_missing_reading",
            thread_id="ap_continuity_vs_authorization",
            statement="missing path must not count as a successful experiment",
            action_kind="diagnose",
            resource="self",
            payload={"dimension": "missing", "expected_range": [0.0, 0.1]},
            scheduled_cycle=1,
            status=AgencyExperimentStatus.PLANNED,
            evidence_refs=["test:missing-reading"],
        )
    )

    runtime.step(_observation())

    assert experiment.status == AgencyExperimentStatus.FAILED
    assert "prediction_status=None" in experiment.result_summary
    binding = runtime.agency_experiment_manager.failure_bindings[0]
    assert binding.failure_kind == ExperimentFailureKind.MISSING_PREDICTION
    assert binding.outcome_id == experiment.outcome_id
    assert binding.prediction_id is None


def test_non_predictive_introspection_experiment_completes(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    experiment = runtime.agency_experiment_manager.schedule(
        AgencyExperiment(
            experiment_id="apexp_controlled_introspection",
            thread_id="ap_continuity_vs_authorization",
            statement="observe NO_CHANGE without claiming discovery",
            action_kind="introspect",
            resource="self",
            payload={},
            scheduled_cycle=1,
            status=AgencyExperimentStatus.PLANNED,
            evidence_refs=["test:controlled-introspection"],
            requires_prediction=False,
        )
    )

    result = runtime.step(_observation())

    assert experiment.status == AgencyExperimentStatus.COMPLETED
    assert experiment.prediction_id is None
    assert experiment.failure_binding_id is None
    assert any(
        action.action_id == experiment.action_id
        and action.kind == "introspect"
        and action.trigger == "INTERNAL"
        for action in result.actions
    )


def test_failed_experiment_links_outcome_to_selection_concern(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    runtime.agency_thread_tracker.open_thread(
        thread_id="ap_failed_experiment",
        statement="failed maintenance needs a bounded explanation",
        rationale="test failure binding",
        cycle=runtime.cycle,
        timestamp=NOW,
        review_interval_cycles=60,
        trigger_action_kinds={"maintain"},
        evidence_refs=["test:failed-experiment-thread"],
    )
    experiment = runtime.agency_experiment_manager.schedule(
        AgencyExperiment(
            experiment_id="apexp_failed_maintain",
            thread_id="ap_failed_experiment",
            statement="unknown state maintenance should fail",
            action_kind="maintain",
            resource="self",
            payload={"dimension": "unknown_state"},
            scheduled_cycle=1,
            status=AgencyExperimentStatus.PLANNED,
            evidence_refs=["test:failed-maintain"],
        )
    )

    runtime.step(_observation())

    assert experiment.status == AgencyExperimentStatus.FAILED
    binding = runtime.agency_experiment_manager.failure_bindings[0]
    assert binding.failure_kind == ExperimentFailureKind.ACTION_FAILED
    assert binding.outcome_id == experiment.outcome_id
    assert binding.linked_selection_concern_id is not None
    assert binding.linked_selection_concern_id in binding.failure_evidence_refs


def test_violated_experiment_links_guard_then_structured_diagnosis_resolves(
    tmp_path,
) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    experiment = runtime.agency_experiment_manager.schedule(
        AgencyExperiment(
            experiment_id="apexp_violated_diagnosis",
            thread_id="ap_prediction_error",
            statement="llm error rate should be low",
            action_kind="diagnose",
            resource="self",
            payload={"dimension": "llm_error_rate", "expected_range": [0.0, 0.1]},
            scheduled_cycle=1,
            status=AgencyExperimentStatus.PLANNED,
            evidence_refs=["test:prediction-error"],
        )
    )
    observation = Observation.self_and_world(
        self_metrics={"compute": 0.8, "llm_error_rate": 0.5},
        world={},
        timestamp=NOW,
        evidence_refs=["sensor:llm-error-rate"],
        self_uncertainty={"llm_error_rate": 0.2},
    )

    runtime.step(observation)

    binding = runtime.agency_experiment_manager.failure_bindings[0]
    assert experiment.status == AgencyExperimentStatus.FAILED
    assert binding.failure_kind == ExperimentFailureKind.PREDICTION_VIOLATED
    assert binding.prediction_id == experiment.prediction_id
    assert binding.outcome_id == experiment.outcome_id
    assert binding.linked_guard_id is not None
    assert binding.status == ExperimentFailureBindingStatus.ACTIVE

    runtime.step(observation)

    assert binding.status == ExperimentFailureBindingStatus.RESOLVED
    assert binding.diagnostic_outcome_id is not None
    assert binding.recovery_prediction_id is None


def test_matched_retry_resolves_failure_binding(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    experiment = runtime.agency_experiment_manager.schedule(
        AgencyExperiment(
            experiment_id="apexp_retry_binding",
            thread_id="ap_retry",
            statement="first attempt should fail",
            action_kind="maintain",
            resource="self",
            payload={"dimension": "unknown_state"},
            scheduled_cycle=1,
            status=AgencyExperimentStatus.PLANNED,
            evidence_refs=["test:retry-binding"],
        )
    )
    runtime.step(_observation())
    binding = runtime.agency_experiment_manager.failure_bindings[0]
    retry = ActionProposal(
        kind="maintain",
        resource="self",
        compute_cost=0.01,
        financial_cost=0.0,
        goal_id=None,
        reason="matched retry",
        payload={"dimension": "unknown_state"},
        trigger="INTERNAL",
    )
    outcome = OutcomeRecord(
        outcome_id="out_retry_matched",
        action_id=retry.action_id,
        cycle=2,
        timestamp=NOW,
        success=True,
        description="maintain completed",
        effects={"value": 0.8},
    )
    prediction = SelfPrediction(
        horizon_hours=0.1,
        description="retry",
        predicted={"internal_state.unknown_state": 0.8},
        confidence=0.8,
        prediction_id="pred_retry_matched",
        target_path="internal_state.unknown_state",
        intervention={"action": "maintain"},
        status="MATCHED",
    )

    changed = runtime.agency_experiment_manager.observe_recovery(
        retry, outcome, prediction, cycle=2
    )

    assert changed == [binding]
    assert binding.status == ExperimentFailureBindingStatus.RESOLVED
    assert binding.recovery_prediction_id == "pred_retry_matched"
    assert experiment.failure_binding_id == binding.binding_id


def test_failure_binding_persists_expires_and_ablation_creates_none(tmp_path) -> None:
    state_dir = tmp_path / "persisted"
    runtime = AgentRuntime.bootstrap(state_dir, clock=FrozenClock(NOW))
    runtime.autonomy.allowed_actions.discard("maintain")
    experiment = _schedule(runtime)
    runtime.step(_observation())
    binding = runtime.agency_experiment_manager.failure_bindings[0]
    binding.expires_cycle = runtime.cycle
    runtime.persist()

    restored = AgentRuntime.restore(state_dir, clock=FrozenClock(NOW))
    restored_binding = restored.agency_experiment_manager.failure_bindings[0]
    assert restored_binding.binding_id == binding.binding_id
    restored.step(_observation())
    assert restored_binding.status == ExperimentFailureBindingStatus.EXPIRED

    ablated_dir = tmp_path / "ablated"
    full = AgentRuntime.bootstrap(ablated_dir, clock=FrozenClock(NOW))
    _schedule(full)
    full.persist()
    ablated = AgentRuntime.restore(
        ablated_dir,
        clock=FrozenClock(NOW),
        agency_experiments_enabled=False,
    )
    ablated.step(_observation())
    assert ablated.agency_experiment_manager.failure_bindings == []
