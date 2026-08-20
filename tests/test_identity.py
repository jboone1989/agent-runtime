from __future__ import annotations

from datetime import datetime, timezone

from agent_runtime.clock import FrozenClock
from agent_runtime.identity import create_genesis_identity, is_same_agent
from agent_runtime.identity.lineage import ContinuityKind, ReplicaHandle, SuccessorRef


def test_genesis_identity_is_self_rooted() -> None:
    clock = FrozenClock(datetime(2026, 8, 20, tzinfo=timezone.utc))
    ident = create_genesis_identity("0.1.0", clock)
    assert ident.agent_id == ident.genesis_id == ident.lineage_root
    assert ident.parent_agent_id is None
    assert is_same_agent(ident, ident)


def test_replica_keeps_identity_offspring_would_not() -> None:
    clock = FrozenClock(datetime(2026, 8, 20, tzinfo=timezone.utc))
    ident = create_genesis_identity("0.1.0", clock)
    replica = ReplicaHandle(agent_id=ident.agent_id, replica_id="r1", started_at=clock.now())
    successor = SuccessorRef(agent_id=ident.agent_id, from_version="0.1.0", to_version="0.2.0")
    assert replica.kind is ContinuityKind.REPLICA
    assert successor.kind is ContinuityKind.SUCCESSOR
    assert replica.agent_id == successor.agent_id == ident.agent_id
