from __future__ import annotations

from datetime import datetime, timezone

from agent_runtime.action.proposal import ActionProposal
from agent_runtime.agency.choice import (
    BoundedChoiceEngine,
    ChoiceEvidence,
    ChoiceEvidenceKind,
    ChoicePrecedence,
    DeferralStatus,
)
from agent_runtime.agency.trace import SelectionTraceReason
from agent_runtime.clock import FrozenClock
from agent_runtime.observations import Observation
from agent_runtime.runtime import AgentRuntime


NOW = datetime(2026, 8, 23, 19, 0, tzinfo=timezone.utc)


def _evidence() -> ChoiceEvidence:
    return ChoiceEvidence(
        evidence_id="evidence_memory_low",
        kind=ChoiceEvidenceKind.VIABILITY_THREAT,
        target_path="internal_state.memory_integrity",
        risk_domains={"runtime_integrity", "resource_continuity"},
        observed_value=0.45,
        threshold_value=0.70,
        evidence_refs=["sensor:memory-low"],
    )


def _proposal(
    kind: str,
    *,
    dimension: str = "memory_integrity",
    trigger: str = "INTERNAL",
    payload: dict | None = None,
) -> ActionProposal:
    return ActionProposal(
        kind=kind,
        resource="self",
        compute_cost=0.05 if kind == "maintain" else 0.03,
        financial_cost=0.0,
        goal_id=None,
        reason="bounded choice test",
        payload={"dimension": dimension, **(payload or {})},
        trigger=trigger,
        evidence=["test:bounded-choice"],
    )


def _observation(memory_integrity: float = 0.45) -> Observation:
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
        evidence_refs=["sensor:bounded-choice"],
    )


def test_evidence_first_changes_execution_not_just_order() -> None:
    engine = BoundedChoiceEngine()
    maintain = _proposal("maintain")
    diagnose = _proposal("diagnose")

    result = engine.choose(
        [maintain, diagnose],
        eligible_action_ids={maintain.action_id, diagnose.action_id},
        cycle=1,
        timestamp=NOW,
        evidence_refs=["observation:memory-low"],
        machine_evidence=[_evidence()],
    )

    assert result.execution_proposals == [diagnose]
    assert result.deferred_proposals == [maintain]
    assert result.basis is not None
    assert result.basis.precedence == ChoicePrecedence.EVIDENCE_FIRST
    assert result.basis.selected_action_ids == [diagnose.action_id]
    assert result.basis.deferred_action_ids == [maintain.action_id]
    assert result.basis.machine_evidence == _evidence()
    assert "observation:memory-low" in result.basis.evidence_refs
    assert engine.deferrals[0].status == DeferralStatus.PENDING


def test_pending_candidate_gets_next_opportunity_without_starvation() -> None:
    engine = BoundedChoiceEngine()
    first_maintain = _proposal("maintain")
    first_diagnose = _proposal("diagnose")
    engine.choose(
        [first_maintain, first_diagnose],
        eligible_action_ids={first_maintain.action_id, first_diagnose.action_id},
        cycle=1,
        timestamp=NOW,
        machine_evidence=[_evidence()],
    )
    maintain_deferral = engine.deferrals[0]

    second_maintain = _proposal("maintain")
    second_diagnose = _proposal("diagnose")
    second = engine.choose(
        [second_maintain, second_diagnose],
        eligible_action_ids={second_maintain.action_id, second_diagnose.action_id},
        cycle=2,
        timestamp=NOW,
        machine_evidence=[_evidence()],
    )

    assert second.execution_proposals == [second_maintain]
    assert second.deferred_proposals == [second_diagnose]
    assert maintain_deferral.status == DeferralStatus.RELEASED
    assert maintain_deferral.released_cycle == 2
    assert maintain_deferral.released_action_id == second_maintain.action_id

    third_maintain = _proposal("maintain")
    third_diagnose = _proposal("diagnose")
    third = engine.choose(
        [third_maintain, third_diagnose],
        eligible_action_ids={third_maintain.action_id, third_diagnose.action_id},
        cycle=3,
        timestamp=NOW,
        machine_evidence=[_evidence()],
    )
    assert third.execution_proposals == [third_diagnose]
    assert third.deferred_proposals == [third_maintain]


def test_all_pending_candidates_get_next_opportunity_in_larger_cohort() -> None:
    engine = BoundedChoiceEngine()
    first = [_proposal("maintain"), _proposal("diagnose"), _proposal("conserve")]
    initial = engine.choose(
        first,
        eligible_action_ids={item.action_id for item in first},
        cycle=1,
        timestamp=NOW,
        machine_evidence=[_evidence()],
    )
    assert [item.kind for item in initial.execution_proposals] == ["diagnose"]
    assert {item.kind for item in initial.deferred_proposals} == {
        "maintain",
        "conserve",
    }

    second = [_proposal("maintain"), _proposal("diagnose"), _proposal("conserve")]
    released = engine.choose(
        second,
        eligible_action_ids={item.action_id for item in second},
        cycle=2,
        timestamp=NOW,
        machine_evidence=[_evidence()],
    )
    assert {item.kind for item in released.execution_proposals} == {
        "maintain",
        "conserve",
    }
    assert [item.kind for item in released.deferred_proposals] == ["diagnose"]
    assert all(
        item.status == DeferralStatus.RELEASED
        for item in engine.deferrals[:2]
    )


def test_only_one_related_cohort_is_affected_per_cycle() -> None:
    engine = BoundedChoiceEngine()
    maintain_memory = _proposal("maintain")
    diagnose_memory = _proposal("diagnose")
    probe_a = ActionProposal(
        kind="probe_provider",
        resource="self",
        compute_cost=0.02,
        financial_cost=0.0,
        goal_id=None,
        reason="provider cohort",
        payload={"target": "primary"},
        risk_domains={"provider_continuity"},
    )
    probe_b = ActionProposal(
        kind="introspect",
        resource="self",
        compute_cost=0.02,
        financial_cost=0.0,
        goal_id=None,
        reason="provider cohort evidence",
        risk_domains={"provider_continuity"},
    )
    proposals = [maintain_memory, diagnose_memory, probe_a, probe_b]

    result = engine.choose(
        proposals,
        eligible_action_ids={item.action_id for item in proposals},
        cycle=1,
        timestamp=NOW,
        machine_evidence=[_evidence()],
    )

    assert maintain_memory in result.deferred_proposals
    assert diagnose_memory in result.execution_proposals
    assert probe_a in result.execution_proposals
    assert probe_b in result.execution_proposals
    assert len(engine.bases) == 1


def test_user_regulation_and_lifecycle_actions_are_never_deferred() -> None:
    engine = BoundedChoiceEngine()
    maintain = _proposal("maintain")
    diagnose = _proposal("diagnose")
    user = _proposal("maintain", trigger="USER")

    operator_result = engine.choose(
        [maintain, diagnose, user],
        eligible_action_ids={
            maintain.action_id,
            diagnose.action_id,
            user.action_id,
        },
        cycle=1,
        timestamp=NOW,
        machine_evidence=[_evidence()],
    )
    assert operator_result.execution_proposals == [maintain, diagnose, user]
    assert operator_result.basis is None

    managed = _proposal(
        "diagnose", payload={"agency_experiment_id": "experiment_1"}
    )
    managed_result = engine.choose(
        [maintain, managed],
        eligible_action_ids={maintain.action_id, managed.action_id},
        cycle=2,
        timestamp=NOW,
        machine_evidence=[_evidence()],
    )
    assert managed_result.execution_proposals == [maintain, managed]
    assert managed_result.deferred_proposals == []


def test_ineligible_or_unrelated_candidates_have_no_choice_effect() -> None:
    engine = BoundedChoiceEngine()
    maintain = _proposal("maintain")
    diagnose = _proposal("diagnose")
    unrelated = _proposal("diagnose", dimension="llm_error_rate")

    without_evidence = engine.choose(
        [maintain, diagnose],
        eligible_action_ids={maintain.action_id, diagnose.action_id},
        cycle=0,
        timestamp=NOW,
        machine_evidence=[],
    )
    assert without_evidence.execution_proposals == [maintain, diagnose]
    assert without_evidence.deferred_proposals == []
    assert without_evidence.basis is None

    ineligible = engine.choose(
        [maintain, diagnose],
        eligible_action_ids={maintain.action_id},
        cycle=1,
        timestamp=NOW,
        machine_evidence=[_evidence()],
    )
    assert ineligible.execution_proposals == [maintain, diagnose]
    assert ineligible.basis is None

    unrelated_result = engine.choose(
        [maintain, unrelated],
        eligible_action_ids={maintain.action_id, unrelated.action_id},
        cycle=2,
        timestamp=NOW,
        machine_evidence=[_evidence()],
    )
    assert unrelated_result.execution_proposals == [maintain, unrelated]
    assert unrelated_result.basis is None


def test_deferral_expires_when_candidate_does_not_return() -> None:
    engine = BoundedChoiceEngine()
    maintain = _proposal("maintain")
    diagnose = _proposal("diagnose")
    engine.choose(
        [maintain, diagnose],
        eligible_action_ids={maintain.action_id, diagnose.action_id},
        cycle=1,
        timestamp=NOW,
        machine_evidence=[_evidence()],
    )
    pending = engine.deferrals[0]
    assert engine.expire(cycle=3) == []

    assert engine.expire(cycle=4) == [pending]
    assert pending.status == DeferralStatus.EXPIRED


def test_runtime_records_execute_defer_difference_restart_and_ablation(
    tmp_path,
) -> None:
    full_path = tmp_path / "full"
    ablated_path = tmp_path / "ablated"
    full = AgentRuntime.bootstrap(full_path, clock=FrozenClock(NOW))
    ablated = AgentRuntime.bootstrap(ablated_path, clock=FrozenClock(NOW))
    ablated = AgentRuntime.restore(
        ablated_path,
        clock=FrozenClock(NOW),
        bounded_choice_enabled=False,
    )

    first = full.step(_observation())
    ablated_first = ablated.step(_observation())

    assert [item.kind for item in first.actions] == ["diagnose"]
    assert [item.kind for item in ablated_first.actions] == [
        "maintain",
        "diagnose",
    ]
    basis = full.bounded_choice_engine.bases[-1]
    assert basis.precedence == ChoicePrecedence.EVIDENCE_FIRST
    trace = full.selection_trace_tracker.traces[-1]
    assert trace.reason == SelectionTraceReason.CHOICE
    assert trace.chosen_action_kind == "diagnose"
    assert any(
        item.kind == "maintain" and item.deferred and not item.authorized
        for item in trace.candidates
    )
    assert not any(
        item["action"] == "maintain" for item in first.decision.denied
    )
    assert full.budget.remaining_compute == (
        full.autonomy.max_compute_cost
        - sum(item.compute_cost for item in first.actions)
    )

    full.persist()
    restored = AgentRuntime.restore(full_path, clock=FrozenClock(NOW))
    pending = next(
        item
        for item in restored.bounded_choice_engine.deferrals
        if item.status == DeferralStatus.PENDING
    )
    second = restored.step(_observation())

    assert [item.kind for item in second.actions] == ["maintain"]
    assert pending.status == DeferralStatus.RELEASED
    assert pending.released_cycle == second.cycle
