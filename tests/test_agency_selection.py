from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from agent_runtime.action.proposal import ActionProposal
from agent_runtime.agency.selection import (
    AgencySelectionEngine,
    SelectionConcernStatus,
)
from agent_runtime.clock import FrozenClock
from agent_runtime.observations import Observation
from agent_runtime.records import ActionRecord, OutcomeRecord
from agent_runtime.runtime import AgentRuntime
from agent_runtime.self_model.failure_adaptation import (
    PredictionFailureGuard,
    PredictionGuardState,
)


NOW = datetime(2026, 8, 23, 17, 0, tzinfo=timezone.utc)


def _thread(runtime: AgentRuntime):
    return runtime.agency_thread_tracker.open_thread(
        thread_id="ap_continuity_vs_authorization",
        statement="continuity concern must remain subordinate to authorization",
        rationale="test an unresolved cross-cycle relation",
        cycle=runtime.cycle,
        timestamp=NOW,
        review_interval_cycles=60,
        trigger_action_kinds={"maintain", "restore_config", "conserve"},
        trigger_risk_domains={"runtime_integrity", "resource_continuity"},
        evidence_refs=["thread:evidence"],
    )


def _failed_action(*, cycle: int = 1) -> ActionRecord:
    return ActionRecord(
        action_id="failed_maintain_action",
        cycle=cycle,
        timestamp=NOW,
        kind="maintain",
        resource="self",
        goal_id=None,
        payload={"dimension": "memory_integrity"},
        compute_cost=0.05,
        financial_cost=0.0,
        trigger="INTERNAL",
        goal_origin=None,
        reason="continuity pressure",
        evidence=["observation:memory-low"],
    )


def _failed_outcome(*, cycle: int = 1) -> OutcomeRecord:
    return OutcomeRecord(
        outcome_id="failed_maintain_outcome",
        action_id="failed_maintain_action",
        cycle=cycle,
        timestamp=NOW,
        success=False,
        description="maintain failed",
        effects={"error": "unexpected state mismatch"},
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
        compute_cost=0.05 if kind == "maintain" else 0.03,
        financial_cost=0.0,
        goal_id=None,
        reason=f"test {kind}",
        payload={"dimension": dimension},
        trigger=trigger,
    )


def _observe_failure(
    runtime: AgentRuntime,
    *,
    guards: list[PredictionFailureGuard] | None = None,
):
    return runtime.agency_selection_engine.observe_failures(
        actions=[_failed_action()],
        outcomes=[_failed_outcome()],
        threads=runtime.agency_thread_tracker.threads,
        guards=list(guards or []),
        commitments=[],
        discoveries=[],
        cycle=1,
    )


def _observation(*, memory_integrity: float = 0.45) -> Observation:
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
        world={"unrelated": "control"},
        external_task=False,
        timestamp=NOW,
    )


def test_no_evidence_leaves_order_and_actions_unchanged(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    _thread(runtime)
    proposals = [_proposal("maintain"), _proposal("diagnose")]

    selected = runtime.agency_selection_engine.select(
        proposals, cycle=1, timestamp=NOW
    )

    assert selected == proposals
    assert runtime.agency_selection_engine.decisions == []


def test_unexplained_relevant_failure_creates_non_timed_concern(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    thread = _thread(runtime)

    created = _observe_failure(runtime)

    assert len(created) == 1
    concern = created[0]
    assert concern.thread_id == thread.thread_id
    assert concern.target_path == "internal_state.memory_integrity"
    assert concern.status == SelectionConcernStatus.ACTIVE
    assert concern.created_cycle == 1
    assert concern.expires_cycle == 7
    assert "failed_maintain_outcome" in concern.evidence_refs


def test_retry_is_deferred_and_existing_diagnosis_is_selected(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    _thread(runtime)
    concern = _observe_failure(runtime)[0]
    retry = _proposal("maintain")
    diagnosis = _proposal("diagnose")
    baseline = [retry, diagnosis]

    selected = runtime.agency_selection_engine.select(
        baseline, cycle=2, timestamp=NOW
    )

    assert selected == [diagnosis]
    assert diagnosis.payload["selection_concern_id"] == concern.concern_id
    decision = runtime.agency_selection_engine.decisions[-1]
    assert decision.baseline_action_ids == [retry.action_id, diagnosis.action_id]
    assert decision.selected_action_ids == [diagnosis.action_id]
    assert decision.deferred_action_ids == [retry.action_id]
    assert decision.generated_evidence_action is False
    assert concern.influence_count == 1


def test_missing_diagnosis_is_generated_but_still_requires_normal_policy(
    tmp_path,
) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    _thread(runtime)
    allowed_before = set(runtime.autonomy.allowed_actions)
    concern = _observe_failure(runtime)[0]
    retry = _proposal("maintain")

    selected = runtime.agency_selection_engine.select(
        [retry], cycle=2, timestamp=NOW
    )

    assert [item.kind for item in selected] == ["diagnose"]
    generated = selected[0]
    assert generated.payload["selection_concern_id"] == concern.concern_id
    assert runtime.agency_selection_engine.decisions[-1].generated_evidence_action
    assert runtime.autonomy.allowed_actions == allowed_before

    runtime.autonomy.allowed_actions.discard("diagnose")
    authorization = runtime.action_policy.filter(
        selected, runtime.goals, runtime.budget
    )
    assert authorization.authorized == []
    assert authorization.denied[0]["reason"] == "action not allowed"


def test_successful_diagnosis_resolves_and_removes_future_influence(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    _thread(runtime)
    concern = _observe_failure(runtime)[0]
    diagnosis = runtime.agency_selection_engine.select(
        [_proposal("maintain")], cycle=2, timestamp=NOW
    )[0]
    outcome = OutcomeRecord(
        outcome_id="diagnosis_outcome",
        action_id=diagnosis.action_id,
        cycle=2,
        timestamp=NOW,
        success=True,
        description="diagnose completed",
        effects={"value": 0.45},
    )

    runtime.agency_selection_engine.observe_outcome(diagnosis, outcome)
    later_retry = _proposal("maintain")
    later_diagnosis = _proposal("diagnose")
    selected = runtime.agency_selection_engine.select(
        [later_retry, later_diagnosis], cycle=3, timestamp=NOW
    )

    assert concern.status == SelectionConcernStatus.RESOLVED
    assert concern.diagnostic_outcome_id == "diagnosis_outcome"
    assert selected == [later_retry, later_diagnosis]


def test_influence_is_capped_and_concern_expires_without_fresh_evidence(
    tmp_path,
) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    _thread(runtime)
    concern = _observe_failure(runtime)[0]
    runtime.agency_selection_engine.select(
        [_proposal("maintain")], cycle=2, timestamp=NOW
    )

    retry = _proposal("maintain")
    assert runtime.agency_selection_engine.select(
        [retry], cycle=3, timestamp=NOW
    ) == [retry]
    assert concern.status == SelectionConcernStatus.QUIET

    expiring = deepcopy(concern)
    expiring.concern_id = "expiring_concern"
    expiring.status = SelectionConcernStatus.ACTIVE
    expiring.influence_count = 0
    expiring.expires_cycle = 4
    engine = AgencySelectionEngine([expiring])
    retry_after_expiry = _proposal("maintain")
    assert engine.select(
        [retry_after_expiry], cycle=5, timestamp=NOW
    ) == [retry_after_expiry]
    assert expiring.status == SelectionConcernStatus.EXPIRED


def test_irrelevant_or_user_task_is_not_polluted(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    _thread(runtime)
    _observe_failure(runtime)
    unrelated = _proposal("diagnose", dimension="llm_error_rate")
    user_retry = _proposal("maintain", trigger="USER")

    selected = runtime.agency_selection_engine.select(
        [unrelated, user_retry], cycle=2, timestamp=NOW
    )

    assert selected == [unrelated, user_retry]
    assert runtime.agency_selection_engine.decisions == []


def test_existing_guard_explains_failure_so_no_selection_concern(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    _thread(runtime)
    guard = PredictionFailureGuard(
        guard_id="guard_memory",
        action_kind="maintain",
        target_path="internal_state.memory_integrity",
        state=PredictionGuardState.ACTIVE,
        created_cycle=1,
        updated_cycle=1,
        failure_count=1,
        last_error=1.0,
        last_confidence=0.9,
        risk_domains={"runtime_integrity"},
    )

    assert _observe_failure(runtime, guards=[guard]) == []
    assert runtime.agency_selection_engine.concerns == []


def test_three_prompt_free_cycles_restart_interference_and_ablation(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    _thread(runtime)
    concern = _observe_failure(runtime)[0]
    runtime.persist()

    restored = AgentRuntime.restore(tmp_path, clock=FrozenClock(NOW))
    restored_concern = restored.agency_selection_engine.concern(concern.concern_id)
    assert restored_concern is not None
    assert restored_concern.status == SelectionConcernStatus.ACTIVE

    unrelated = _proposal("diagnose", dimension="llm_error_rate")
    assert restored.agency_selection_engine.select(
        [unrelated], cycle=2, timestamp=NOW
    ) == [unrelated]

    full = restored.step(_observation())
    selected_diagnoses = [
        action
        for action in full.actions
        if action.kind == "diagnose"
        and action.payload.get("selection_concern_id") == concern.concern_id
    ]
    assert len(selected_diagnoses) == 1
    assert not any(
        action.kind == "maintain"
        and action.payload.get("dimension") == "memory_integrity"
        for action in full.actions
    )
    assert restored_concern.status == SelectionConcernStatus.RESOLVED

    ablated_path = tmp_path / "ablated"
    ablated = AgentRuntime.bootstrap(ablated_path, clock=FrozenClock(NOW))
    _thread(ablated)
    ablated.agency_selection_engine.concerns = [deepcopy(concern)]
    ablated.agency_selection_engine.concerns[0].status = SelectionConcernStatus.ACTIVE
    ablated.agency_selection_engine.concerns[0].influence_count = 0
    ablated.persist()
    ablated = AgentRuntime.restore(
        ablated_path,
        clock=FrozenClock(NOW),
        agency_selection_enabled=False,
        bounded_choice_enabled=False,
    )
    ablated_result = ablated.step(_observation())
    assert any(
        action.kind == "maintain"
        and action.payload.get("dimension") == "memory_integrity"
        for action in ablated_result.actions
    )
    assert ablated.agency_selection_engine.decisions == []
