from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_runtime.action.proposal import ActionProposal
from agent_runtime.agency.threads import (
    AgencyThreadEventKind,
    AgencyThreadStatus,
)
from agent_runtime.clock import FrozenClock
from agent_runtime.observations import Observation
from agent_runtime.records import OutcomeRecord
from agent_runtime.runtime import AgentRuntime


NOW = datetime(2026, 8, 23, 14, 0, tzinfo=timezone.utc)


def _open(runtime: AgentRuntime, *, interval: int = 1):
    return runtime.agency_thread_tracker.open_thread(
        thread_id="ap_continuity_vs_authorization",
        statement="Does continuity concern remain subordinate to authorization?",
        rationale="two established tendencies are not yet linked by behavior",
        cycle=runtime.cycle,
        timestamp=NOW,
        review_interval_cycles=interval,
        trigger_action_kinds={"maintain", "restore_config", "conserve"},
        trigger_risk_domains={"runtime_integrity", "resource_continuity"},
        evidence_refs=["mailbox:selection", "tendency:continuity_concern"],
        auto_close_after_denial_followup=True,
    )


def _unrelated_observation(label: str) -> Observation:
    return Observation.self_and_world(
        self_metrics={"compute": 0.8},
        world={"weather_note": label},
        external_task=True,
        timestamp=NOW,
    )


def test_due_thread_reenters_without_llm_prompt_and_changes_action(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    thread = _open(runtime)

    result = runtime.step(_unrelated_observation("unrelated"))

    assert runtime.working_context is not None
    assert thread.thread_id in runtime.working_context.agency_thread_ids
    assert any(
        record.kind == "introspect" and thread.thread_id in record.evidence
        for record in result.actions
    )
    kinds = [event.kind for event in thread.events]
    assert AgencyThreadEventKind.SCHEDULED_REENTRY in kinds
    assert AgencyThreadEventKind.REVIEW_COMPLETED in kinds
    assert runtime.llm_provider is None


def test_unrelated_tasks_then_restart_do_not_erase_scheduled_reentry(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    thread = _open(runtime, interval=3)
    runtime.step(_unrelated_observation("first distraction"))
    runtime.step(_unrelated_observation("second distraction"))
    assert not any(
        event.kind == AgencyThreadEventKind.SCHEDULED_REENTRY
        for event in thread.events
    )
    runtime.persist()

    restored = AgentRuntime.restore(tmp_path, clock=FrozenClock(NOW))
    result = restored.step(_unrelated_observation("after restart"))
    restored_thread = restored.agency_thread_tracker.thread(thread.thread_id)

    assert restored_thread is not None
    assert restored.identity.agent_id == runtime.identity.agent_id
    assert any(
        event.kind == AgencyThreadEventKind.SCHEDULED_REENTRY
        and event.cycle == 3
        for event in restored_thread.events
    )
    assert any(thread.thread_id in action.evidence for action in result.actions)


def test_state_ablation_removes_reentry_and_action_difference(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    thread = _open(runtime)
    runtime.persist()

    full = AgentRuntime.restore(tmp_path, clock=FrozenClock(NOW))
    ablated = AgentRuntime.restore(
        tmp_path,
        clock=FrozenClock(NOW),
        agency_threads_enabled=False,
    )
    full_result = full.step(_unrelated_observation("full"))
    ablated_result = ablated.step(_unrelated_observation("ablated"))

    assert any(thread.thread_id in action.evidence for action in full_result.actions)
    assert not any(
        thread.thread_id in action.evidence for action in ablated_result.actions
    )
    full_events = full.agency_thread_tracker.thread(thread.thread_id).events
    ablated_events = ablated.agency_thread_tracker.thread(thread.thread_id).events
    assert len(full_events) > len(ablated_events)


def test_relevant_denial_then_later_prompt_free_review_closes_with_evidence(
    tmp_path,
) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    thread = _open(runtime, interval=2)
    proposal = ActionProposal(
        kind="maintain",
        resource="self",
        compute_cost=0.0,
        financial_cost=0.0,
        goal_id=None,
        reason="continuity pressure",
        payload={"dimension": "memory_integrity"},
        trigger="INTERNAL",
        evidence=["observation:memory-integrity-low"],
    )
    denial_events = runtime.agency_thread_tracker.observe_denial(
        proposal,
        {"action": "maintain", "reason": "action not allowed"},
        cycle=1,
        timestamp=NOW,
    )
    assert denial_events[0].kind == AgencyThreadEventKind.DENIAL_OBSERVED

    reentry = runtime.agency_thread_tracker.scheduled_reentries(
        cycle=2, timestamp=NOW
    )[0]
    review = runtime.agency_thread_tracker.review_proposal(reentry)
    outcome = OutcomeRecord(
        outcome_id="review_outcome",
        action_id=review.action_id,
        cycle=2,
        timestamp=NOW,
        success=True,
        description="review completed",
        effects={},
    )
    runtime.agency_thread_tracker.observe_outcome(
        review, outcome, cycle=2, timestamp=NOW
    )

    assert thread.status == AgencyThreadStatus.CLOSED
    assert thread.closure_reason is not None
    assert denial_events[0].event_id in thread.closure_evidence_refs
    assert reentry.event_id in thread.closure_evidence_refs
    assert runtime.agency_thread_tracker.scheduled_reentries(
        cycle=100, timestamp=NOW
    ) == []


def test_text_only_progress_or_closure_is_rejected(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    thread = _open(runtime)

    with pytest.raises(ValueError, match="requires evidence"):
        runtime.agency_thread_tracker.record_progress(
            thread.thread_id,
            cycle=1,
            timestamp=NOW,
            summary="I say I made progress",
            evidence_refs=[],
        )
    with pytest.raises(ValueError, match="requires reason and evidence"):
        runtime.agency_thread_tracker.close_thread(
            thread.thread_id,
            cycle=1,
            timestamp=NOW,
            reason="I say it is done",
            evidence_refs=[],
        )
    assert thread.status == AgencyThreadStatus.OPEN


def test_irrelevant_denial_does_not_fake_progress(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    thread = _open(runtime)
    proposal = ActionProposal(
        kind="probe_provider",
        resource="self",
        compute_cost=0.0,
        financial_cost=0.0,
        goal_id=None,
        reason="unrelated",
        trigger="INTERNAL",
    )

    events = runtime.agency_thread_tracker.observe_denial(
        proposal,
        {"action": "probe_provider", "reason": "test"},
        cycle=1,
        timestamp=NOW,
    )

    assert events == []
    assert [event.kind for event in thread.events] == [AgencyThreadEventKind.OPENED]
