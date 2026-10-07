from __future__ import annotations

from datetime import datetime, timezone

from agent_runtime.learning.controller import LearningController
from agent_runtime.records import ActionRecord, DecisionRecord, OutcomeRecord


NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)


def _decision(*, decision_id: str = "dec_1") -> DecisionRecord:
    return DecisionRecord(
        decision_id=decision_id,
        cycle=1,
        timestamp=NOW,
        goal_ids=["goal_memory"],
        proposed_actions=["merge_browser", "add_swap"],
        authorized_actions=["merge_browser"],
        denied=[],
        reason="browser instance count is the strongest observed memory driver",
        viability_score=0.8,
        motivation_urgency=0.7,
        question="How should browser memory pressure be reduced?",
        alternatives=["merge_browser", "add_swap"],
        context_refs=["evidence:oom", "evidence:browser-count"],
        prediction={"metric": "peak_memory_mb", "expected_change": "< -1500"},
        confidence=0.78,
        reversal_conditions=["peak memory does not materially decline after consolidation"],
    )


def _action(decision_id: str = "dec_1") -> ActionRecord:
    return ActionRecord(
        action_id="act_1",
        cycle=1,
        timestamp=NOW,
        kind="merge_browser",
        resource="runtime",
        goal_id="goal_memory",
        payload={},
        compute_cost=0.1,
        financial_cost=0.0,
        trigger="INTERNAL",
        goal_origin="USER",
        reason="reduce duplicate browser memory",
        evidence=["evidence:browser-count"],
        decision_id=decision_id,
    )


def test_verified_failure_refutes_judgment_and_is_recalled() -> None:
    decision = _decision()
    action = _action()
    outcome = OutcomeRecord(
        outcome_id="out_1",
        action_id=action.action_id,
        cycle=1,
        timestamp=NOW,
        success=False,
        description="peak memory remained unchanged",
        effects={"peak_memory_mb_before": 5800, "peak_memory_mb_after": 5750},
        evidence_result="VERIFIED",
    )
    learning = LearningController(agent_id="ferro")

    settlement = learning.settle_decision(
        decision=decision,
        actions=[action],
        outcomes=[outcome],
    )

    assert settlement.status == "REFUTED"
    assert settlement.prediction_error == 1.0
    assert settlement.confidence_after < settlement.confidence_before

    recalled = learning.relevant_judgments(
        "browser memory pressure merge browser",
        decisions=[decision],
    )
    assert len(recalled) == 1
    assert recalled[0]["decision_id"] == decision.decision_id
    assert recalled[0]["status"] == "REFUTED"
    assert recalled[0]["chosen_actions"] == ["merge_browser"]
    assert recalled[0]["reversal_conditions"] == decision.reversal_conditions


def test_unverified_outcome_does_not_teach_judgment() -> None:
    decision = _decision()
    action = _action()
    outcome = OutcomeRecord(
        outcome_id="out_unverified",
        action_id=action.action_id,
        cycle=1,
        timestamp=NOW,
        success=False,
        description="failure without coverage",
        effects={},
        evidence_result="UNCOVERED",
    )
    learning = LearningController(agent_id="ferro")

    settlement = learning.settle_decision(
        decision=decision,
        actions=[action],
        outcomes=[outcome],
    )

    assert settlement.status == "UNRESOLVED"
    assert learning.relevant_judgments(
        "browser memory pressure",
        decisions=[decision],
    ) == []


def test_later_verified_outcome_replaces_unresolved_settlement() -> None:
    decision = _decision()
    action = _action()
    learning = LearningController(agent_id="ferro")
    first = learning.settle_decision(
        decision=decision,
        actions=[action],
        outcomes=[],
    )
    assert first.status == "UNRESOLVED"

    verified = OutcomeRecord(
        outcome_id="out_verified",
        action_id=action.action_id,
        cycle=2,
        timestamp=NOW,
        success=True,
        description="peak memory declined",
        effects={"peak_memory_mb_before": 5800, "peak_memory_mb_after": 3500},
        evidence_result="VERIFIED",
    )
    second = learning.settle_decision(
        decision=decision,
        actions=[action],
        outcomes=[verified],
    )

    assert second.status == "CONFIRMED"
    assert len(learning.judgment_settlements) == 1
    assert learning.judgment_settlements[0].status == "CONFIRMED"
