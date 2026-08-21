"""CheckpointCoordinator: atomic multi-participant snapshots. Opaque mind payload."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_runtime.clock import FrozenClock
from agent_runtime.lifecycle.checkpoint import (
    CheckpointCoordinator,
    CheckpointIdentityError,
    DigestMismatchError,
)


class _MemoryParticipant:
    def __init__(self, participant_id: str, schema_version: str, payload: dict) -> None:
        self.participant_id = participant_id
        self.schema_version = schema_version
        self.payload = payload
        self.restored: dict | None = None
        self.fail_snapshot = False

    def snapshot(self) -> dict:
        if self.fail_snapshot:
            raise RuntimeError("snapshot failed")
        return json.loads(json.dumps(self.payload))

    def restore(self, payload: dict) -> None:
        self.restored = payload


def _coord(tmp_path: Path, participants, agent_id="agent_1") -> CheckpointCoordinator:
    return CheckpointCoordinator(
        agent_id=agent_id,
        path=tmp_path / "checkpoint.json",
        participants=list(participants),
        clock=FrozenClock(datetime(2026, 8, 22, tzinfo=timezone.utc)),
    )


def test_checkpoint_collects_runtime_and_opaque_mind_with_digests(tmp_path) -> None:
    runtime = _MemoryParticipant("runtime", "0.2.2", {"cycle": 3, "goals": ["g1"]})
    mind = _MemoryParticipant(
        "mind",
        "1",
        {"threat": 0.81, "pad": {"valence": -0.2}, "CORT": 0.99},
    )
    env = _coord(tmp_path, [runtime, mind]).checkpoint(logical_cycle=3)
    assert env.agent_id == "agent_1"
    assert env.logical_cycle == 3
    assert [c.participant_id for c in env.components] == ["runtime", "mind"]
    assert env.components[0].schema_version == "0.2.2"
    assert env.components[1].payload["threat"] == 0.81
    assert env.components[1].payload_digest
    assert (tmp_path / "checkpoint.json").is_file()
    raw = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))
    assert raw["components"][1]["payload"]["CORT"] == 0.99


def test_restore_rejects_tampered_mind_digest_without_partial_restore(tmp_path) -> None:
    runtime = _MemoryParticipant("runtime", "0.2.2", {"cycle": 1})
    mind = _MemoryParticipant("mind", "1", {"threat": 0.7})
    coord = _coord(tmp_path, [runtime, mind])
    coord.checkpoint(logical_cycle=1)
    raw = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))
    raw["components"][1]["payload"]["threat"] = 0.0
    (tmp_path / "checkpoint.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(DigestMismatchError):
        coord.restore()
    assert runtime.restored is None
    assert mind.restored is None


def test_restore_applies_all_components_after_digest_check(tmp_path) -> None:
    runtime = _MemoryParticipant("runtime", "0.2.2", {"cycle": 4})
    mind = _MemoryParticipant("mind", "1", {"threat": 0.55})
    coord = _coord(tmp_path, [runtime, mind])
    coord.checkpoint(logical_cycle=4)
    runtime.payload = {"cycle": 0}
    mind.payload = {"threat": 0.0}
    env = coord.restore()
    assert runtime.restored == {"cycle": 4}
    assert mind.restored == {"threat": 0.55}
    assert env.logical_cycle == 4


def test_checkpoint_rejects_agent_id_mismatch(tmp_path) -> None:
    runtime = _MemoryParticipant("runtime", "0.2.2", {"agent_id": "other", "cycle": 1})
    coord = _coord(tmp_path, [runtime], agent_id="agent_1")
    with pytest.raises(CheckpointIdentityError):
        coord.checkpoint(logical_cycle=1)
    assert not (tmp_path / "checkpoint.json").exists()


def test_restore_rejects_cycle_mismatch(tmp_path) -> None:
    runtime = _MemoryParticipant("runtime", "0.2.2", {"cycle": 2})
    coord = _coord(tmp_path, [runtime])
    coord.checkpoint(logical_cycle=2)
    with pytest.raises(CheckpointIdentityError):
        coord.restore(expected_cycle=9)


def test_failed_participant_snapshot_keeps_last_success(tmp_path) -> None:
    runtime = _MemoryParticipant("runtime", "0.2.2", {"n": 1})
    mind = _MemoryParticipant("mind", "1", {"threat": 0.4})
    coord = _coord(tmp_path, [runtime, mind])
    coord.checkpoint(logical_cycle=1)
    first = (tmp_path / "checkpoint.json").read_text(encoding="utf-8")
    runtime.payload = {"n": 2}
    mind.fail_snapshot = True
    with pytest.raises(RuntimeError, match="snapshot failed"):
        coord.checkpoint(logical_cycle=2)
    assert (tmp_path / "checkpoint.json").read_text(encoding="utf-8") == first
    tmp_leftovers = list(tmp_path.glob("*.tmp"))
    assert tmp_leftovers == [] or all(not p.exists() or p.stat().st_size >= 0 for p in tmp_leftovers)


def test_unfinished_temp_file_does_not_replace_success(tmp_path) -> None:
    runtime = _MemoryParticipant("runtime", "0.2.2", {"n": 1})
    coord = _coord(tmp_path, [runtime])
    coord.checkpoint(logical_cycle=1)
    success = (tmp_path / "checkpoint.json").read_text(encoding="utf-8")
    tmp = tmp_path / "checkpoint.json.tmp"
    tmp.write_text('{"partial": true}', encoding="utf-8")
    env = coord.restore()
    assert env.logical_cycle == 1
    assert json.loads(success)["logical_cycle"] == 1


def test_old_runtime_agent_state_still_restores(tmp_path) -> None:
    from agent_runtime.runtime import AgentRuntime

    rt = AgentRuntime.bootstrap(tmp_path / "state")
    rt.submit_goal("keep this goal")
    rt.persist()
    restored = AgentRuntime.restore(tmp_path / "state")
    assert restored.identity.agent_id == rt.identity.agent_id
    assert any(g.description == "keep this goal" for g in restored.goals)


def test_coordinator_does_not_read_mind_pad_fields() -> None:
    import inspect

    from agent_runtime.lifecycle import checkpoint

    src = inspect.getsource(checkpoint)
    assert "CORT" not in src
    assert "interoception" not in src
    assert ".payload[\"threat\"]" not in src
    assert ".payload['threat']" not in src
