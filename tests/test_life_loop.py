"""Life Loop closed loops: ContractMotivationEngine + ContractV1AgencySession."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytest.importorskip("virtualbrain")

from virtualbrain.adapter.service import ContractV1AgencySession  # noqa: E402

from agent_runtime.clock import FrozenClock
from agent_runtime.embodiment import digital_pack, mock_physical_pack
from agent_runtime.motivation.contract_engine import ContractMotivationEngine
from agent_runtime.motivation.port import InProcessMotivationPort
from agent_runtime.runtime import AgentRuntime
from agent_runtime.survival.failover import ProviderPool


def _clock() -> FrozenClock:
    return FrozenClock(datetime(2026, 8, 20, tzinfo=timezone.utc))


def _attach_session(rt: AgentRuntime) -> ContractV1AgencySession:
    session = ContractV1AgencySession(rt.identity.agent_id)
    rt.motivation_engine = ContractMotivationEngine(
        InProcessMotivationPort(session),
        agent_id=session.agent_id,
    )
    return session


def test_closed_loop_digital_resolved_threat(tmp_path) -> None:
    pool = ProviderPool(active="primary", names=["primary", "backup"])
    rt = AgentRuntime.bootstrap(
        tmp_path / "d",
        clock=_clock(),
        providers=pool,
        embodiment=digital_pack(providers=pool),
    )
    session = _attach_session(rt)
    resting = session.snapshot()["affect"]["arousal"]
    rt.environment.apply("providers/primary/error_rate", 0.72)
    rt.environment.apply("providers/backup/error_rate", 0.02)
    result = rt.tick()
    assert any(a.kind == "switch_provider" for a in result.local_actions)
    assert all(e.code != "COGNITIVE_CAPABILITY_AT_RISK" for e in result.escalations)
    assert result.motivation.arousal > resting
    assert result.motivation.threat < 0.5
    assert not any(
        "capability" in g.description.lower() and g.origin.value == "VIABILITY"
        for g in result.goals_created
    )


def test_closed_loop_digital_unresolved_threat(tmp_path) -> None:
    pool = ProviderPool(active="primary", names=["primary", "backup"])
    rt = AgentRuntime.bootstrap(
        tmp_path / "d2",
        clock=_clock(),
        providers=pool,
        embodiment=digital_pack(providers=pool),
    )
    _attach_session(rt)
    rt.environment.apply("providers/primary/error_rate", 0.95)
    rt.environment.apply("providers/backup/error_rate", 0.95)
    result = rt.tick()
    assert any(e.code == "COGNITIVE_CAPABILITY_AT_RISK" for e in result.escalations)
    assert result.motivation.threat >= 0.7
    internals = [g for g in rt.goals if g.origin.value in {"VIABILITY", "INTERNAL"}]
    assert internals
    assert internals[0].evidence_refs


def test_closed_loop_mock_physical_battery(tmp_path) -> None:
    pack = mock_physical_pack()
    pack.environment.apply("battery", 0.10)
    rt = AgentRuntime.bootstrap(tmp_path / "p", clock=_clock(), embodiment=pack)
    session = _attach_session(rt)
    resting = session.snapshot()["affect"]["arousal"]
    result = rt.tick()
    assert any(a.kind == "conserve" for a in result.local_actions) or rt.regulatory_state.resource_conservation >= 0.6
    assert type(rt).__name__ == "AgentRuntime"
    directions = {t.action for t in result.motivation.action_tendencies}
    assert "conserve" in directions or any(
        t.reason.startswith("temporary compatibility:REST")
        or t.reason.startswith("temporary compatibility:AVOID")
        for t in result.motivation.action_tendencies
    )
    assert result.motivation.arousal >= resting


def test_closed_loop_restart_continuity(tmp_path) -> None:
    pool = ProviderPool(active="primary", names=["primary", "backup"])
    rt = AgentRuntime.bootstrap(
        tmp_path / "r",
        clock=_clock(),
        providers=pool,
        embodiment=digital_pack(providers=pool),
    )
    session = _attach_session(rt)
    rt.environment.apply("providers/primary/error_rate", 0.72)
    rt.environment.apply("providers/backup/error_rate", 0.02)
    for _ in range(10):
        rt.tick()
    pre = session.snapshot()
    goals_before = {g.description for g in rt.goals}
    rt.persist()
    mind = session.snapshot()
    session2 = ContractV1AgencySession(session.agent_id)
    session2.restore(mind)
    rt2 = AgentRuntime.restore(
        tmp_path / "r",
        clock=_clock(),
        motivation_engine=ContractMotivationEngine(
            InProcessMotivationPort(session2),
            agent_id=session2.agent_id,
        ),
    )
    assert rt2.identity.agent_id == rt.identity.agent_id
    assert {g.description for g in rt2.goals} == goals_before
    post = session2.snapshot()
    assert post["affect"]["threat"] == pytest.approx(pre["affect"]["threat"], abs=1e-6)
    assert post["affect"]["valence"] == pytest.approx(pre["affect"]["valence"], abs=1e-6)
    for name in ("stress", "danger"):
        assert post["interoception"]["estimates"][name] == pytest.approx(
            pre["interoception"]["estimates"][name], abs=1e-6
        )
    result = rt2.tick()
    fresh = ContractV1AgencySession(session.agent_id)
    baseline = fresh.snapshot()["affect"]["threat"]
    assert result.motivation.threat > baseline
