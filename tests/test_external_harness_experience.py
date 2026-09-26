from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_runtime.clock import FrozenClock
from agent_runtime.runtime import AgentRuntime


NOW = datetime(2026, 9, 26, 13, 0, tzinfo=timezone.utc)


def _boom(*args, **kwargs):
    raise AssertionError("external experience ingestion must not execute a second action loop")


def test_external_harness_experience_advances_learning_without_planning_or_actuation(
    tmp_path, monkeypatch
):
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    monkeypatch.setattr(runtime.planner, "plan", _boom)
    monkeypatch.setattr(runtime.actuation_gateway, "execute", _boom)

    receipt = runtime.observe_external_experience(
        source="ferro-wakeful",
        action_kind="OBSERVE_WORLD",
        success=True,
        description="fresh public evidence changed the world model",
        world_updates={
            "last_wakeful_task": "OBSERVE_WORLD",
            "last_task_status": "OK",
        },
        evidence_refs=["world-evidence:1", "task-result:1"],
        goal_id="autonomous-goal:1",
        world_utility=0.4,
        timestamp=NOW,
    )

    assert receipt["cycle"] == 1
    assert receipt["success"] is True
    assert receipt["world_utility"] == 0.4
    assert runtime.cycle == 1
    assert runtime.records.actions[-1].kind == "OBSERVE_WORLD"
    assert runtime.records.actions[-1].trigger == "EXTERNAL_HARNESS"
    assert runtime.records.outcomes[-1].effects["world_utility"] == 0.4
    assert runtime.experience_traces[-1].logical_cycle == 1
    assert runtime.evolution_experiences[-1].logical_cycle == 1
    assert runtime.evolution_evidence_ledger.items[-1].outcome_success is True
    assert runtime.records.reflections[-1].cycle == 1
    assert runtime.world_model.state.environment["last_wakeful_task"] == "OBSERVE_WORLD"

    runtime.persist()
    restored = AgentRuntime.restore(tmp_path, clock=FrozenClock(NOW))
    assert restored.cycle == 1
    assert restored.records.actions[-1].kind == "OBSERVE_WORLD"
    assert restored.records.outcomes[-1].outcome_id == receipt["outcome_id"]


def test_external_harness_failure_becomes_evolution_failure_evidence(tmp_path):
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))

    runtime.observe_external_experience(
        source="ferro-wakeful",
        action_kind="RESOURCE_REVIEW",
        success=False,
        description="resource review produced no viable recovery",
        evidence_refs=["task-result:failed"],
        world_utility=-0.3,
        timestamp=NOW,
    )

    assert runtime.cycle == 1
    assert runtime.records.outcomes[-1].success is False
    assert any(
        item.kind.value == "FAILURE"
        and item.subject == "RESOURCE_REVIEW"
        for item in runtime.evolution.state.facts
    )


def test_external_harness_rejects_invalid_world_utility(tmp_path):
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))

    with pytest.raises(ValueError, match="world_utility"):
        runtime.observe_external_experience(
            source="ferro-wakeful",
            action_kind="REFLECT",
            success=True,
            description="invalid utility",
            world_utility=2.0,
            timestamp=NOW,
        )

    assert runtime.cycle == 0


def test_external_harness_task_result_is_exactly_once(tmp_path):
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    first = runtime.observe_external_experience(
        source="ferro-wakeful",
        action_kind="OBSERVE_WORLD",
        success=True,
        description="fresh evidence",
        evidence_refs=["task-result:stable-1", "world-evidence:1"],
        world_utility=0.2,
        timestamp=NOW,
    )
    action_count = len(runtime.records.actions)
    outcome_count = len(runtime.records.outcomes)
    trace_count = len(runtime.experience_traces)

    second = runtime.observe_external_experience(
        source="ferro-wakeful",
        action_kind="OBSERVE_WORLD",
        success=True,
        description="same durable result replayed",
        evidence_refs=["task-result:stable-1", "world-evidence:1"],
        world_utility=0.2,
        timestamp=NOW,
    )

    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["action_id"] == first["action_id"]
    assert second["outcome_id"] == first["outcome_id"]
    assert second["experience_id"] == first["experience_id"]
    assert runtime.cycle == 1
    assert len(runtime.records.actions) == action_count
    assert len(runtime.records.outcomes) == outcome_count
    assert len(runtime.experience_traces) == trace_count
