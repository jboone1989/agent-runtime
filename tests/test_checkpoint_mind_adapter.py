"""Mind participant is opaque to CheckpointCoordinator. Optional VirtualBrain."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

pytest.importorskip("virtualbrain")

from virtualbrain.adapter.service import ContractV1AgencySession  # noqa: E402

from agent_runtime.clock import FrozenClock
from agent_runtime.embodiment import digital_pack
from agent_runtime.lifecycle.checkpoint import CheckpointCoordinator, DigestMismatchError
from agent_runtime.lifecycle.participant import OpaqueParticipant
from agent_runtime.motivation.contract_engine import ContractMotivationEngine
from agent_runtime.motivation.port import InProcessMotivationPort
from agent_runtime.runtime import AgentRuntime
from agent_runtime.survival.failover import ProviderPool


class _RuntimeProbe:
    def __init__(self, rt: AgentRuntime) -> None:
        self.participant_id = "runtime"
        self.schema_version = "0.2.2"
        self._rt = rt
        self.restored = False

    def snapshot(self) -> dict:
        return {"agent_id": self._rt.identity.agent_id, "cycle": self._rt.cycle}

    def restore(self, payload: dict) -> None:
        self.restored = True
        self.last = payload


def _clock() -> FrozenClock:
    return FrozenClock(datetime(2026, 8, 22, tzinfo=timezone.utc))


def test_runtime_cycle_matches_mind_tick_and_checkpoint_envelope(tmp_path) -> None:
    pool = ProviderPool(active="primary", names=["primary", "backup"])
    rt = AgentRuntime.bootstrap(
        tmp_path / "s",
        clock=_clock(),
        providers=pool,
        embodiment=digital_pack(providers=pool),
    )
    session = ContractV1AgencySession(rt.identity.agent_id)
    rt.motivation_engine = ContractMotivationEngine(
        InProcessMotivationPort(session),
        agent_id=session.agent_id,
    )
    rt.environment.apply("providers/primary/error_rate", 0.95)
    rt.environment.apply("providers/backup/error_rate", 0.95)
    rt.tick()
    assert session.tick == rt.cycle
    probe = _RuntimeProbe(rt)
    mind = OpaqueParticipant(session)
    coord = CheckpointCoordinator(
        agent_id=rt.identity.agent_id,
        path=tmp_path / "checkpoint.json",
        participants=[probe, mind],
        clock=_clock(),
    )
    env = coord.checkpoint(logical_cycle=rt.cycle)
    assert env.logical_cycle == rt.cycle == session.tick


def test_tampered_mind_digest_does_not_partially_restore(tmp_path) -> None:
    pool = ProviderPool(active="primary", names=["primary", "backup"])
    rt = AgentRuntime.bootstrap(
        tmp_path / "t",
        clock=_clock(),
        providers=pool,
        embodiment=digital_pack(providers=pool),
    )
    session = ContractV1AgencySession(rt.identity.agent_id)
    rt.motivation_engine = ContractMotivationEngine(
        InProcessMotivationPort(session),
        agent_id=session.agent_id,
    )
    rt.environment.apply("providers/primary/error_rate", 0.95)
    rt.environment.apply("providers/backup/error_rate", 0.95)
    rt.tick()
    pre = session.snapshot()
    probe = _RuntimeProbe(rt)
    coord = CheckpointCoordinator(
        agent_id=rt.identity.agent_id,
        path=tmp_path / "checkpoint.json",
        participants=[probe, OpaqueParticipant(session)],
        clock=_clock(),
    )
    coord.checkpoint(logical_cycle=rt.cycle)
    raw = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))
    mind_comp = next(c for c in raw["components"] if c["participant_id"] == "mind")
    mind_comp["payload"]["affect"]["threat"] = 0.0
    (tmp_path / "checkpoint.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(DigestMismatchError):
        coord.restore()
    assert probe.restored is False
    assert session.snapshot()["affect"]["threat"] == pytest.approx(pre["affect"]["threat"], abs=1e-6)
    assert session.snapshot()["affect"]["threat"] != 0.0
