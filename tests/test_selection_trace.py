from __future__ import annotations

from datetime import datetime, timezone

from agent_runtime.action.proposal import ActionProposal
from agent_runtime.agency.selection import (
    AgencySelectionEngine,
    SelectionConcern,
    SelectionConcernStatus,
)
from agent_runtime.agency.trace import (
    SelectionTraceReason,
    SelectionTraceStatus,
    SelectionTraceTracker,
)
from agent_runtime.clock import FrozenClock
from agent_runtime.observations import Observation
from agent_runtime.runtime import AgentRuntime
from agent_runtime.self_model.beliefs import SelfPrediction


NOW = datetime(2026, 8, 23, 18, 0, tzinfo=timezone.utc)


def _observation(memory_integrity: float = 0.9) -> Observation:
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
        evidence_refs=["sensor:selection-trace"],
    )


def _proposal(
    kind: str,
    *,
    dimension: str = "memory_integrity",
    trigger: str = "INTERNAL",
) -> ActionProposal:
    return ActionProposal(
        kind=kind,
        resource="self",
        compute_cost=0.01,
        financial_cost=0.0,
        goal_id=None,
        reason="selection trace test",
        payload={"dimension": dimension},
        trigger=trigger,
        evidence=["test:candidate"],
    )


def test_baseline_internal_choice_is_traced_once_and_persists(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))

    result = runtime.step(_observation())

    assert len(runtime.selection_trace_tracker.traces) == 1
    trace = runtime.selection_trace_tracker.traces[0]
    chosen = next(item for item in result.actions if item.trigger == "INTERNAL")
    assert trace.chosen_action_id == chosen.action_id
    assert trace.reason == SelectionTraceReason.BASELINE
    assert trace.status == SelectionTraceStatus.ACTIVE
    assert trace.stale_after_cycle == result.cycle + 24
    assert trace.candidates
    assert any(item.authorized for item in trace.candidates)

    runtime.persist()
    restored = AgentRuntime.restore(tmp_path, clock=FrozenClock(NOW))
    assert restored.selection_trace_tracker.traces[0].trace_id == trace.trace_id
    assert restored.selection_trace_tracker.traces[0].reason == (
        SelectionTraceReason.BASELINE
    )


def test_guard_diagnosis_trace_has_machine_evidence(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    runtime.self_model.reconcile_observation(_observation(0.55))
    prediction = SelfPrediction(
        horizon_hours=0.0,
        description="maintain should improve memory integrity",
        predicted={"internal_state.memory_integrity": 0.9},
        confidence=0.8,
        prediction_id="trace_prediction_failed",
        target_path="internal_state.memory_integrity",
        intervention={"action": "maintain", "action_id": "trace_failed_action"},
        created_at=NOW,
        evidence_refs=["trace_failure_outcome"],
        status="VIOLATED",
        observed=0.55,
        error=0.35,
    )
    guard = runtime.failure_adaptation.observe_prediction(prediction, cycle=0)

    runtime.step(_observation(0.55))

    trace = runtime.selection_trace_tracker.traces[-1]
    assert trace.reason == SelectionTraceReason.GUARD
    assert trace.chosen_action_kind == "diagnose"
    assert trace.considered_path == "internal_state.memory_integrity"
    assert guard.guard_id in trace.trigger_evidence_refs
    assert "trace_prediction_failed" in trace.trigger_evidence_refs
    assert any(
        item.kind == "maintain" and not item.authorized
        for item in trace.candidates
    )


def test_concern_trace_uses_selection_decision_not_posthoc_text() -> None:
    retry = _proposal("maintain")
    diagnosis = _proposal("diagnose")
    concern = SelectionConcern(
        concern_id="concern_memory",
        thread_id="thread_memory",
        action_kind="maintain",
        target_path="internal_state.memory_integrity",
        risk_domains={"runtime_integrity"},
        status=SelectionConcernStatus.ACTIVE,
        created_cycle=1,
        expires_cycle=7,
        evidence_refs=["failed_action", "failed_outcome"],
    )
    engine = AgencySelectionEngine([concern])
    selected = engine.select([retry, diagnosis], cycle=2, timestamp=NOW)
    tracker = SelectionTraceTracker()

    trace = tracker.record(
        cycle=2,
        timestamp=NOW,
        proposals=selected,
        authorized=selected,
        selection_decisions=engine.decisions,
        guards=[],
    )

    assert trace is not None
    assert trace.reason == SelectionTraceReason.CONCERN
    assert trace.chosen_action_id == diagnosis.action_id
    assert engine.decisions[0].decision_id in trace.trigger_evidence_refs
    assert tracker.record(
        cycle=2,
        timestamp=NOW,
        proposals=selected,
        authorized=selected,
        selection_decisions=engine.decisions,
        guards=[],
    ) is None


def test_user_and_regulation_only_actions_are_not_traced() -> None:
    tracker = SelectionTraceTracker()
    user = _proposal("diagnose", trigger="USER")
    regulation = _proposal("diagnose", trigger="REGULATION")

    trace = tracker.record(
        cycle=1,
        timestamp=NOW,
        proposals=[user, regulation],
        authorized=[user, regulation],
        selection_decisions=[],
        guards=[],
    )

    assert trace is None
    assert tracker.traces == []


def test_trace_becomes_stale_and_never_changes_actions(tmp_path) -> None:
    traced_path = tmp_path / "traced"
    ablated_path = tmp_path / "ablated"
    traced = AgentRuntime.bootstrap(traced_path, clock=FrozenClock(NOW))
    ablated = AgentRuntime.bootstrap(ablated_path, clock=FrozenClock(NOW))
    ablated = AgentRuntime.restore(
        ablated_path,
        clock=FrozenClock(NOW),
        selection_tracing_enabled=False,
    )

    full_result = traced.step(_observation())
    ablated_result = ablated.step(_observation())

    assert [item.kind for item in full_result.actions] == [
        item.kind for item in ablated_result.actions
    ]
    assert len(traced.selection_trace_tracker.traces) == 1
    assert ablated.selection_trace_tracker.traces == []
    trace = traced.selection_trace_tracker.traces[0]
    assert traced.selection_trace_tracker.expire(
        cycle=trace.stale_after_cycle
    ) == []
    assert traced.selection_trace_tracker.expire(
        cycle=trace.stale_after_cycle + 1
    ) == [trace]
    assert trace.status == SelectionTraceStatus.STALE

