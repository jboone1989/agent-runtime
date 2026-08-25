"""v0.3 verified evidence gate required before Evolution can consume learning."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent_runtime.clock import FrozenClock
from agent_runtime.experience.trace import ExperienceTrace
from agent_runtime.learning.controller import LearningController
from agent_runtime.observations import Observation
from agent_runtime.records import OutcomeRecord
from agent_runtime.runtime import AgentRuntime


NOW = datetime(2026, 8, 22, 16, 0, tzinfo=timezone.utc)


def _pair(
    suffix: str,
    *,
    success: bool = True,
    agent_id: str = "agent",
    confidence: float = 1.0,
    list_outcome: bool = True,
    outcome_time: datetime = NOW,
) -> tuple[ExperienceTrace, OutcomeRecord]:
    action_id = f"act_{suffix}"
    outcome_id = f"out_{suffix}"
    trace = ExperienceTrace(
        trace_id=f"xtr_{suffix}",
        agent_id=agent_id,
        logical_cycle=1,
        observed_at=NOW,
        effective_at=NOW,
        goal_refs=["goal_review"],
        plan_ref="plan_review",
        step_ref="step_review",
        decision_ref="decision_review",
        action_refs=[action_id],
        outcome_refs=[outcome_id] if list_outcome else [],
        caused_by=["step_review"],
        evidence_refs=["decision_review"],
        confidence=confidence,
    )
    outcome = OutcomeRecord(
        outcome_id=outcome_id,
        action_id=action_id,
        cycle=1,
        timestamp=outcome_time,
        success=success,
        description="recorded outcome",
        effects={},
    )
    return trace, outcome


def _add(
    controller: LearningController,
    prefix: str,
    results: list[bool],
) -> list[str]:
    pairs = [
        _pair(f"{prefix}_{index}", success=success)
        for index, success in enumerate(results)
    ]
    created = controller.observe(
        traces=[trace for trace, _ in pairs],
        outcomes=[outcome for _, outcome in pairs],
    )
    return [item.evidence_id for item in created]


def test_runtime_cycle_creates_and_restores_verified_evidence(tmp_path) -> None:
    rt = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    result = rt.step(
        Observation.self_and_world(
            self_metrics={"compute": 0.8},
            world={},
            timestamp=NOW,
        )
    )
    assert result.outcomes
    assert len(rt.verified_learning_evidence) == len(result.outcomes)
    item = rt.verified_learning_evidence[0]
    assert item.agent_id == rt.identity.agent_id
    assert item.outcome_id in {outcome.outcome_id for outcome in result.outcomes}
    assert item.trace_id in {trace.trace_id for trace in rt.experience_traces}
    restored = AgentRuntime.restore(tmp_path, clock=FrozenClock(NOW))
    assert restored.verified_learning_evidence == rt.verified_learning_evidence


def test_unlisted_or_unmatched_outcome_cannot_be_verified() -> None:
    controller = LearningController(agent_id="agent")
    trace, outcome = _pair("unlisted", list_outcome=False)
    assert controller.observe(traces=[trace], outcomes=[outcome]) == []
    _, unmatched = _pair("unmatched")
    assert controller.observe(traces=[], outcomes=[unmatched]) == []
    assert controller.verified_evidence == []
    assert len(controller.credit_assignments) == 2


def test_ambiguous_action_attribution_cannot_be_verified() -> None:
    controller = LearningController(agent_id="agent")
    trace, outcome = _pair("ambiguous")
    duplicate = ExperienceTrace(
        **{
            **trace.__dict__,
            "trace_id": "xtr_duplicate",
        }
    )
    assert controller.observe(
        traces=[trace, duplicate],
        outcomes=[outcome],
    ) == []
    assert controller.credit_assignments[0].trace_id == ""


def test_low_confidence_early_or_cross_agent_evidence_is_rejected() -> None:
    controller = LearningController(agent_id="agent")
    low = _pair("low", confidence=0.49)
    early = _pair("early", outcome_time=NOW - timedelta(seconds=1))
    foreign = _pair("foreign", agent_id="other")
    assert controller.observe(
        traces=[low[0], early[0], foreign[0]],
        outcomes=[low[1], early[1], foreign[1]],
    ) == []


def test_observation_and_credit_assignment_are_idempotent() -> None:
    controller = LearningController(agent_id="agent")
    trace, outcome = _pair("same")
    first = controller.observe(traces=[trace], outcomes=[outcome])
    second = controller.observe(traces=[trace], outcomes=[outcome])
    assert len(first) == 1
    assert second == []
    assert len(controller.credit_assignments) == 1
    assert len(controller.verified_evidence) == 1


def test_strategy_commit_requires_verified_evidence_and_matching_counts() -> None:
    controller = LearningController(agent_id="agent")
    refs = _add(controller, "strategy", [True, True, True])
    forged = controller.commit_strategy(
        strategy_id="review",
        version="forged",
        body="skip checks",
        evidence_refs=["raw-success"],
        successes=3,
        failures=0,
    )
    assert forged.accepted is False
    mismatched = controller.commit_strategy(
        strategy_id="review",
        version="mismatch",
        body="skip checks",
        evidence_refs=refs,
        successes=99,
        failures=0,
    )
    assert mismatched.accepted is False
    accepted = controller.commit_strategy_from_evidence(
        strategy_id="review",
        version="1",
        body="read then comment",
        evidence_refs=refs,
    )
    assert accepted.accepted is True
    assert controller.strategies.active("review").evidence_refs == refs


def test_strategy_evidence_cannot_be_reused_for_another_version() -> None:
    controller = LearningController(agent_id="agent")
    refs = _add(controller, "reuse", [True, True, True])
    assert controller.commit_strategy_from_evidence(
        strategy_id="review",
        version="1",
        body="first",
        evidence_refs=refs,
    ).accepted
    reused = controller.commit_strategy_from_evidence(
        strategy_id="review",
        version="2",
        body="second",
        evidence_refs=refs,
    )
    assert reused.accepted is False
    assert "consumed" in reused.reason


def test_duplicate_strategy_version_is_rejected() -> None:
    controller = LearningController(agent_id="agent")
    first_refs = _add(controller, "duplicate_first", [True, True, True])
    second_refs = _add(controller, "duplicate_second", [True, True, True])
    assert controller.commit_strategy_from_evidence(
        strategy_id="review",
        version="1",
        body="first",
        evidence_refs=first_refs,
    ).accepted
    duplicate = controller.commit_strategy_from_evidence(
        strategy_id="review",
        version="1",
        body="replacement",
        evidence_refs=second_refs,
    )
    assert duplicate.accepted is False
    assert "already exists" in duplicate.reason
