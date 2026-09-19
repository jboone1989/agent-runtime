from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from agent_runtime.clock import FrozenClock
from agent_runtime.evolution.experience import (
    EvolutionExperience,
    VersionBinding,
    seal_evolution_experience,
)
from agent_runtime.experience.trace import ExperienceTrace
from agent_runtime.records import ActionRecord, OutcomeRecord
from agent_runtime.runtime import AgentRuntime


NOW = datetime(2026, 9, 19, 13, 0, tzinfo=timezone.utc)


def _trace() -> ExperienceTrace:
    return ExperienceTrace(
        trace_id="trace:1",
        agent_id="agent:ferro",
        logical_cycle=7,
        observed_at=NOW,
        effective_at=NOW,
        goal_refs=["goal:1"],
        plan_ref="plan:1",
        step_ref="step:1",
        decision_ref="decision:1",
        action_refs=["action:1"],
        outcome_refs=["outcome:1"],
        caused_by=["evidence:decision"],
        evidence_refs=["evidence:decision"],
        confidence=0.9,
    )


def _action() -> ActionRecord:
    return ActionRecord(
        action_id="action:1",
        cycle=7,
        timestamp=NOW,
        kind="publish",
        resource="x",
        goal_id="goal:1",
        payload={},
        compute_cost=1.25,
        financial_cost=0.05,
        trigger="INTERNAL",
        goal_origin="INTERNAL",
        reason="test",
        evidence=["evidence:decision"],
    )


def _outcome() -> OutcomeRecord:
    return OutcomeRecord(
        outcome_id="outcome:1",
        action_id="action:1",
        cycle=7,
        timestamp=NOW + timedelta(hours=2),
        success=True,
        description="world settled",
        effects={"followers": 3},
        evidence_result="VERIFIED",
    )


def test_sealed_experience_is_immutable_and_preserves_versions_costs_and_boundary():
    item = seal_evolution_experience(
        _trace(),
        actions=[_action()],
        outcomes=[_outcome()],
        sealed_at=NOW + timedelta(hours=2),
        context_refs=["observation:1", "decision:1"],
        belief_refs=["belief:topic"],
        version_bindings=[
            VersionBinding("capability", "publish", "x-api:v3"),
            VersionBinding("backend", "reasoning", "provider:model"),
        ],
    )

    assert item.compute_cost == 1.25
    assert item.financial_cost == 0.05
    assert item.outcome_known_at == NOW + timedelta(hours=2)
    assert item.context_refs == ("observation:1", "decision:1")
    assert item.belief_refs == ("belief:topic",)
    assert [binding.kind for binding in item.version_bindings] == [
        "backend",
        "capability",
    ]
    with pytest.raises(FrozenInstanceError):
        item.logical_cycle = 9


def test_seal_rejects_missing_referenced_action_or_outcome():
    with pytest.raises(ValueError, match="missing referenced actions"):
        seal_evolution_experience(
            _trace(),
            actions=[],
            outcomes=[_outcome()],
            sealed_at=NOW + timedelta(hours=2),
        )
    with pytest.raises(ValueError, match="missing referenced outcomes"):
        seal_evolution_experience(
            _trace(),
            actions=[_action()],
            outcomes=[],
            sealed_at=NOW + timedelta(hours=2),
        )


def test_runtime_snapshot_round_trips_sealed_evolution_experience(tmp_path):
    rt = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    item = EvolutionExperience(
        experience_id="evx:manual",
        trace_id="trace:manual",
        agent_id=rt.identity.agent_id,
        logical_cycle=0,
        observed_at=NOW,
        effective_at=NOW,
        sealed_at=NOW,
        goal_refs=(),
        plan_ref=None,
        step_ref=None,
        decision_ref=None,
        action_refs=(),
        outcome_refs=(),
        caused_by=(),
        evidence_refs=(),
        context_refs=("observation:manual",),
        belief_refs=(),
        version_bindings=(),
        compute_cost=0.0,
        financial_cost=0.0,
        outcome_known_at=None,
        confidence=1.0,
    )
    rt.evolution_experiences.append(item)
    rt.persist()

    restored = AgentRuntime.restore(tmp_path, clock=FrozenClock(NOW))

    assert restored.evolution_experiences == [item]


def test_runtime_step_seals_one_evolution_experience(tmp_path):
    rt = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))

    result = rt.step()

    assert len(rt.evolution_experiences) == 1
    item = rt.evolution_experiences[0]
    assert item.logical_cycle == result.cycle
    assert item.trace_id == rt.experience_traces[-1].trace_id
    assert item.agent_id == rt.identity.agent_id
    assert item.sealed_at == NOW
