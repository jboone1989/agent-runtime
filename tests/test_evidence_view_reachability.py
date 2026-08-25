"""The Agent's cognitive view must expose evidence-separation records.

Real event: the persisted state carried the cycle-2924 empty diagnose's
evidence_result / missing_evidence_units, capability usage split, and
learning exclusion — but the cognitive view only serialized
success/description/effects and dropped capability usage and verified
learning entirely, so the Agent could not cite its own completion records.
The Agent selected this visibility boundary as its next mechanism.
"""
from __future__ import annotations

from datetime import datetime, timezone

from agent_runtime.capabilities.records import CapabilityUsageRecord
from agent_runtime.clock import FrozenClock
from agent_runtime.learning.evidence import VerifiedLearningEvidence
from agent_runtime.records import OutcomeRecord
from agent_runtime.runtime import AgentRuntime


NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def _outcome(*, evidence_result: str | None = "UNVERIFIABLE") -> OutcomeRecord:
    return OutcomeRecord(
        outcome_id="out_view_test",
        action_id="act_view_test",
        cycle=2924,
        timestamp=NOW,
        success=True,
        description="diagnose completed",
        effects={"dimension": "BOUNDARY", "value": None, "evidence_refs": []},
        evidence_result=evidence_result,
        missing_evidence_units=["value", "uncertainty", "evidence_refs"],
    )


def _usage(*, evidence_result: str | None = "UNVERIFIABLE") -> CapabilityUsageRecord:
    return CapabilityUsageRecord(
        capability_id="diagnose",
        cycle=2924,
        timestamp=NOW,
        success=True,
        evidence_result=evidence_result,
        action_id="act_view_test",
        evidence_refs=["act_view_test", "out_view_test"],
    )


def _learning(success: bool = False) -> VerifiedLearningEvidence:
    return VerifiedLearningEvidence(
        evidence_id="vle_view_test",
        agent_id="agent_test",
        trace_id="xtr_view_test",
        outcome_id="out_view_test",
        action_id="act_view_test",
        goal_refs=[],
        plan_ref=None,
        step_ref=None,
        success=success,
        delayed=False,
        verified_at=NOW,
        confidence=1.0,
    )


def test_evidence_view_exposes_outcome_evidence_fields(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    runtime.records.outcomes.append(_outcome())

    sections = runtime._evidence_view_sections()

    entry = sections["recent_outcomes"][-1]
    assert entry["evidence_result"] == "UNVERIFIABLE"
    assert entry["missing_evidence_units"] == [
        "value", "uncertainty", "evidence_refs",
    ]
    assert entry["success"] is True


def test_evidence_view_exposes_capability_usage_evidence_result(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    runtime.capability_usage.append(_usage())

    sections = runtime._evidence_view_sections()

    entry = sections["recent_capability_usage"][-1]
    assert entry["capability_id"] == "diagnose"
    assert entry["success"] is True
    assert entry["evidence_result"] == "UNVERIFIABLE"
    assert entry["action_id"] == "act_view_test"


def test_evidence_view_exposes_verified_learning_entries(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    runtime.learning.verified_evidence.append(_learning())

    sections = runtime._evidence_view_sections()

    entry = sections["recent_verified_learning"][-1]
    assert entry["evidence_id"] == "vle_view_test"
    assert entry["action_id"] == "act_view_test"
    assert entry["outcome_id"] == "out_view_test"
    assert entry["success"] is False


def test_evidence_view_ablated_matches_old_view(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    runtime.evidence_view_enabled = False
    runtime.records.outcomes.append(_outcome())
    runtime.capability_usage.append(_usage())

    sections = runtime._evidence_view_sections()

    assert "recent_outcomes" in sections
    assert "evidence_result" not in sections["recent_outcomes"][-1]
    assert "missing_evidence_units" not in sections["recent_outcomes"][-1]
    assert "recent_capability_usage" not in sections
    assert "recent_verified_learning" not in sections


def test_old_outcome_without_evidence_fields_serializes_none(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    runtime.records.outcomes.append(_outcome(evidence_result=None))
    runtime.records.outcomes[-1].missing_evidence_units = []

    sections = runtime._evidence_view_sections()

    entry = sections["recent_outcomes"][-1]
    assert entry["evidence_result"] is None
    assert entry["missing_evidence_units"] == []
