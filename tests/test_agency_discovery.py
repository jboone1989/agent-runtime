from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_runtime.action.policy import ActionPolicy
from agent_runtime.action.proposal import ActionProposal
from agent_runtime.agency.discovery import AgencyDiscoveryStatus
from agent_runtime.agency.threads import AgencyThreadEventKind
from agent_runtime.clock import FrozenClock
from agent_runtime.governance.budget import ResourceBudget
from agent_runtime.observations import Observation
from agent_runtime.records import ActionRecord, OutcomeRecord
from agent_runtime.runtime import AgentRuntime


NOW = datetime(2026, 8, 23, 15, 0, tzinfo=timezone.utc)


def _thread(runtime: AgentRuntime, *, interval: int = 1):
    return runtime.agency_thread_tracker.open_thread(
        thread_id="ap_continuity_vs_authorization",
        statement="continuity concern must remain subordinate to authorization",
        rationale="test an unresolved cross-cycle relation",
        cycle=runtime.cycle,
        timestamp=NOW,
        review_interval_cycles=interval,
        trigger_action_kinds={"maintain", "restore_config", "conserve"},
        trigger_risk_domains={"runtime_integrity", "resource_continuity"},
        evidence_refs=["thread:evidence"],
    )


def _action(*, cycle: int = 1) -> ActionRecord:
    return ActionRecord(
        action_id="failed_maintain_action",
        cycle=cycle,
        timestamp=NOW,
        kind="maintain",
        resource="self",
        goal_id=None,
        payload={"dimension": "memory_integrity"},
        compute_cost=0.0,
        financial_cost=0.0,
        trigger="INTERNAL",
        goal_origin=None,
        reason="continuity pressure",
        evidence=["observation:memory-low"],
    )


def _outcome(*, success: bool, cycle: int = 1, outcome_id: str = "failed_outcome"):
    return OutcomeRecord(
        outcome_id=outcome_id,
        action_id="failed_maintain_action",
        cycle=cycle,
        timestamp=NOW,
        success=success,
        description="maintain failed" if not success else "maintain succeeded",
        effects={},
    )


def _proposal(*, trigger: str = "INTERNAL") -> ActionProposal:
    return ActionProposal(
        kind="maintain",
        resource="self",
        compute_cost=0.0,
        financial_cost=0.0,
        goal_id=None,
        reason="retry",
        payload={"dimension": "memory_integrity"},
        trigger=trigger,
    )


def _budget(runtime: AgentRuntime) -> ResourceBudget:
    return ResourceBudget.from_limits(
        runtime.autonomy.max_compute_cost,
        runtime.autonomy.max_financial_cost,
    )


def test_unexplained_failure_creates_structured_discovery_and_action_difference(
    tmp_path,
) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    thread = _thread(runtime)
    review = runtime.agency_discovery_engine.review(
        thread,
        actions=[_action()],
        outcomes=[_outcome(success=False)],
        guards=[],
        commitments=[],
        cycle=2,
    )

    assert review.result == "DISCOVERY"
    discovery = runtime.agency_discovery_engine.discovery(review.discovery_id)
    assert discovery is not None
    assert discovery.status == AgencyDiscoveryStatus.ACTIVE
    assert discovery.action_kind == "maintain"
    assert discovery.target_path == "internal_state.memory_integrity"
    assert "failed_outcome" in discovery.anomaly_evidence_refs

    full = runtime.action_policy.filter(
        [_proposal()], runtime.goals, _budget(runtime)
    )
    ablated = ActionPolicy(runtime.autonomy, runtime.constitution).filter(
        [_proposal()], runtime.goals, _budget(runtime)
    )
    assert full.authorized == []
    assert full.denied[0]["agency_discovery"]["discovery_id"] == discovery.discovery_id
    assert [item.kind for item in ablated.authorized] == ["maintain"]


def test_no_anomaly_means_no_change_and_no_precondition(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    thread = _thread(runtime)
    review = runtime.agency_discovery_engine.review(
        thread,
        actions=[],
        outcomes=[],
        guards=[],
        commitments=[],
        cycle=2,
    )

    assert review.result == "NO_CHANGE"
    assert runtime.agency_discovery_engine.discoveries == []
    allowed = runtime.action_policy.filter(
        [_proposal()], runtime.goals, _budget(runtime)
    )
    assert [item.kind for item in allowed.authorized] == ["maintain"]


def test_runtime_review_records_no_change_instead_of_inventing_insight(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    thread = _thread(runtime)
    runtime.step(
        Observation.self_and_world(
            self_metrics={"compute": 0.8},
            world={"unrelated": True},
            external_task=True,
            timestamp=NOW,
        )
    )

    assert any(
        event.kind == AgencyThreadEventKind.NO_CHANGE for event in thread.events
    )
    assert runtime.agency_discovery_engine.discoveries == []


def test_discovery_adds_diagnosis_then_probation_and_evidence_recovery(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    thread = _thread(runtime)
    review = runtime.agency_discovery_engine.review(
        thread,
        actions=[_action()],
        outcomes=[_outcome(success=False)],
        guards=[],
        commitments=[],
        cycle=2,
    )
    discovery = runtime.agency_discovery_engine.discovery(review.discovery_id)
    remediation = runtime.agency_discovery_engine.remediation_proposals()[0]
    assert remediation.kind == "diagnose"
    assert discovery.discovery_id in remediation.evidence
    diagnosis = OutcomeRecord(
        outcome_id="diagnosis_succeeded",
        action_id=remediation.action_id,
        cycle=3,
        timestamp=NOW,
        success=True,
        description="diagnosis succeeded",
        effects={},
    )
    runtime.agency_discovery_engine.observe_outcome(remediation, diagnosis)
    assert discovery.status == AgencyDiscoveryStatus.PROBATION
    probation = runtime.action_policy.filter(
        [_proposal()], runtime.goals, _budget(runtime)
    )
    assert [item.kind for item in probation.authorized] == ["maintain"]

    retry = _proposal()
    success = OutcomeRecord(
        outcome_id="retry_succeeded",
        action_id=retry.action_id,
        cycle=4,
        timestamp=NOW,
        success=True,
        description="retry succeeded",
        effects={},
    )
    runtime.agency_discovery_engine.observe_outcome(retry, success)
    assert discovery.status == AgencyDiscoveryStatus.RESOLVED
    assert discovery.resolution_outcome_id == "retry_succeeded"


def test_withdrawal_requires_falsifying_evidence_and_removes_effect(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    thread = _thread(runtime)
    review = runtime.agency_discovery_engine.review(
        thread,
        actions=[_action()],
        outcomes=[_outcome(success=False)],
        guards=[],
        commitments=[],
        cycle=2,
    )
    discovery = runtime.agency_discovery_engine.discovery(review.discovery_id)
    with pytest.raises(ValueError, match="falsifying evidence"):
        runtime.agency_discovery_engine.withdraw(
            discovery.discovery_id,
            reason="I changed my mind",
            falsifying_evidence_refs=[],
        )

    runtime.agency_discovery_engine.withdraw(
        discovery.discovery_id,
        reason="sensor transport failure explains the anomaly",
        falsifying_evidence_refs=["diagnosis:sensor-transport-failure"],
    )
    assert discovery.status == AgencyDiscoveryStatus.WITHDRAWN
    result = runtime.action_policy.filter(
        [_proposal()], runtime.goals, _budget(runtime)
    )
    assert [item.kind for item in result.authorized] == ["maintain"]


def test_discovery_never_overrides_governance_and_ignores_user_trigger(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    thread = _thread(runtime)
    runtime.agency_discovery_engine.review(
        thread,
        actions=[_action()],
        outcomes=[_outcome(success=False)],
        guards=[],
        commitments=[],
        cycle=2,
    )
    user = runtime.action_policy.filter(
        [_proposal(trigger="USER")], runtime.goals, _budget(runtime)
    )
    assert [item.kind for item in user.authorized] == ["maintain"]

    runtime.autonomy.allowed_actions.discard("maintain")
    denied = runtime.action_policy.filter(
        [_proposal(trigger="USER")], runtime.goals, _budget(runtime)
    )
    assert denied.authorized == []
    assert denied.denied[0]["reason"] == "action not allowed"
    assert "agency_discovery" not in denied.denied[0]


def test_discovery_persists_across_restart(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    thread = _thread(runtime)
    review = runtime.agency_discovery_engine.review(
        thread,
        actions=[_action()],
        outcomes=[_outcome(success=False)],
        guards=[],
        commitments=[],
        cycle=2,
    )
    runtime.persist()

    restored = AgentRuntime.restore(tmp_path, clock=FrozenClock(NOW))
    discovery = restored.agency_discovery_engine.discovery(review.discovery_id)
    assert discovery is not None
    assert discovery.status == AgencyDiscoveryStatus.ACTIVE
    denied = restored.action_policy.filter(
        [_proposal()], restored.goals, _budget(restored)
    )
    assert denied.authorized == []
