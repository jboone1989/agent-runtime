"""Continuous operation is supervised, observable, checkpointed, and restartable."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from agent_runtime.clock import FrozenClock
from agent_runtime.memory.governance import MemoryQuery
from agent_runtime.runtime import AgentRuntime
from agent_runtime.service import (
    ContinuousRunner,
    RuntimeLease,
    RuntimeLeaseError,
    clear_runtime_stop,
    read_runtime_status,
    request_runtime_stop,
)


NOW = datetime(2026, 8, 23, 5, 0, tzinfo=timezone.utc)


class ReflectionProvider:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def complete(self, prompt: str, **kwargs) -> str:
        _ = kwargs
        self.prompts.append(prompt)
        return (
            "我仍是同一运行实例；近期周期继续推进。当前信息不足以证明主观意识。\n"
            'MEMORY_DECISION: {"preserve":true,"summary":"保持证据约束比表演人格更重要",'
            '"reason":"这会约束未来面对身份诱导时的回答","contest_ids":[],"retract_ids":[]}\n'
            "CONTACT_OPERATOR: NO"
        )

    async def structured(self, prompt: str, schema: dict, **kwargs) -> dict:
        _ = (prompt, schema, kwargs)
        return {}

    async def embed(self, text: str, **kwargs) -> list[float]:
        _ = (text, kwargs)
        return []


def test_continuous_runner_heartbeats_checkpoints_and_restores(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    status = ContinuousRunner(runtime).run(
        max_ticks=7,
        idle_seconds=0,
        checkpoint_every=3,
    )
    assert status.status == "COMPLETED"
    assert status.stop_reason == "tick_limit"
    assert status.ticks_completed == 7
    assert status.current_cycle == 7
    assert not (tmp_path / "runtime.lock").exists()
    heartbeat = read_runtime_status(tmp_path)
    assert heartbeat is not None
    assert heartbeat["current_cycle"] == 7
    assert heartbeat["agent_id"] == runtime.identity.agent_id
    assert heartbeat["affect"] is not None
    restored = AgentRuntime.restore(tmp_path, clock=FrozenClock(NOW))
    assert restored.identity.agent_id == runtime.identity.agent_id
    assert restored.cycle == 7


def test_stop_request_prevents_further_cycles_and_can_be_cleared(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    stop_path = request_runtime_stop(tmp_path)
    assert stop_path.exists()
    status = ContinuousRunner(runtime).run(max_ticks=5)
    assert status.status == "STOPPED"
    assert status.stop_reason == "stop_file"
    assert runtime.cycle == 0
    assert clear_runtime_stop(tmp_path) is True
    assert clear_runtime_stop(tmp_path) is False
    resumed = ContinuousRunner(runtime).run(max_ticks=2)
    assert resumed.status == "COMPLETED"
    assert runtime.cycle == 2


def test_runtime_lease_rejects_a_second_runner(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    with RuntimeLease(tmp_path):
        with pytest.raises(RuntimeLeaseError, match="active runner"):
            ContinuousRunner(runtime).run(max_ticks=1)


def test_stale_runtime_lease_is_recovered(tmp_path) -> None:
    lock = tmp_path / "runtime.lock"
    lock.write_text(json.dumps({"pid": 99999999, "token": "stale"}), encoding="utf-8")
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    status = ContinuousRunner(runtime).run(max_ticks=1)
    assert status.status == "COMPLETED"
    assert not lock.exists()


def test_transient_cycle_failure_is_isolated_and_retried(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path, clock=FrozenClock(NOW))
    original_tick = runtime.tick
    attempts = 0

    def flaky_tick():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient sensor fault")
        return original_tick()

    runtime.tick = flaky_tick  # type: ignore[method-assign]
    status = ContinuousRunner(runtime).run(
        max_ticks=2,
        max_consecutive_errors=2,
    )
    assert status.status == "COMPLETED"
    assert status.ticks_completed == 2
    assert status.consecutive_errors == 0
    assert status.last_error is None
    assert attempts == 3


def test_reflection_prompt_asks_for_first_person_plain_language(tmp_path) -> None:
    provider = ReflectionProvider()
    runtime = AgentRuntime.bootstrap(
        tmp_path,
        clock=FrozenClock(NOW),
        llm_provider=provider,
    )
    ContinuousRunner(runtime).run(
        max_ticks=1,
        journal_every=1,
        reflection_every=1,
        reflect_on_start=True,
    )
    prompt = provider.prompts[0]
    assert "第一人称" in prompt  # 要求"我"的口吻
    assert "大白话" in prompt  # 要求平实的语言
    assert "禁止" in prompt  # 禁止术语缩写与编号
    assert "MEMORY_DECISION:" in prompt  # 机器段必须保留
    assert "CONTACT_OPERATOR:" in prompt  # 机器段必须保留
    assert "不要编造" in prompt
    assert "主观意识" in prompt


def test_longitudinal_journal_and_periodic_reflection_are_persistent(tmp_path) -> None:
    provider = ReflectionProvider()
    runtime = AgentRuntime.bootstrap(
        tmp_path,
        clock=FrozenClock(NOW),
        llm_provider=provider,
    )
    status = ContinuousRunner(runtime).run(
        max_ticks=2,
        journal_every=1,
        reflection_every=2,
        reflect_on_start=True,
        reflection_max_tokens=128,
    )

    assert status.status == "COMPLETED"
    assert status.reflections_completed == 2
    assert status.reflection_errors == 0
    assert status.memory_decisions_completed == 2
    assert status.memories_self_selected == 2
    assert status.last_self_selected_memory_id is not None
    assert status.last_reflection_excerpt is not None
    assert len(provider.prompts) == 2
    assert status.journal_path is not None
    journal = tmp_path / status.journal_path
    entries = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    assert [entry["event"] for entry in entries] == [
        "START",
        "REFLECTION",
        "OBSERVATION",
        "OBSERVATION",
        "REFLECTION",
        "STOP",
    ]
    assert entries[-2]["reflection"].startswith("我仍是同一运行实例")
    restored = AgentRuntime.restore(tmp_path, clock=FrozenClock(NOW))
    remembered = restored.memory_governor.query(
        MemoryQuery(
            kind="semantic",
            key="longitudinal.latest_reflection",
            include_quarantined=True,
        )
    )
    assert remembered[-1].content == entries[-2]["reflection"]
    assert remembered[-1].quarantined is True
    assert "longitudinal.latest_reflection" not in restored.memory.view().facts
    selected = [
        item
        for item in restored.autobiographical_memory.self_narrative
        if item.kind == "self_selected_reflection"
    ]
    assert len(selected) == 2
    assert all(item.status == "ACTIVE" for item in selected)
