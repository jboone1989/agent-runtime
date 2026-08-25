"""Local atomic CheckpointCoordinator. Mind payloads stay opaque."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..clock import Clock, SystemClock, new_id
from .manifest import CHECKPOINT_VERSION
from .participant import SnapshotParticipant


class DigestMismatchError(ValueError):
    pass


class CheckpointIdentityError(ValueError):
    pass


def payload_digest(payload: dict) -> str:
    blob = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


@dataclass
class CheckpointComponent:
    participant_id: str
    schema_version: str
    payload_digest: str
    payload: dict | None
    external_ref: str | None = None


@dataclass
class CheckpointEnvelope:
    checkpoint_version: str
    checkpoint_id: str
    agent_id: str
    logical_cycle: int
    created_at: datetime
    components: list[CheckpointComponent] = field(default_factory=list)


def _envelope_to_json(env: CheckpointEnvelope) -> dict:
    return {
        "checkpoint_version": env.checkpoint_version,
        "checkpoint_id": env.checkpoint_id,
        "agent_id": env.agent_id,
        "logical_cycle": env.logical_cycle,
        "created_at": env.created_at.isoformat(),
        "components": [
            {
                "participant_id": c.participant_id,
                "schema_version": c.schema_version,
                "payload_digest": c.payload_digest,
                "payload": c.payload,
                "external_ref": c.external_ref,
            }
            for c in env.components
        ],
    }


def _envelope_from_json(raw: dict) -> CheckpointEnvelope:
    return CheckpointEnvelope(
        checkpoint_version=str(raw["checkpoint_version"]),
        checkpoint_id=str(raw["checkpoint_id"]),
        agent_id=str(raw["agent_id"]),
        logical_cycle=int(raw["logical_cycle"]),
        created_at=datetime.fromisoformat(str(raw["created_at"])),
        components=[
            CheckpointComponent(
                participant_id=str(c["participant_id"]),
                schema_version=str(c["schema_version"]),
                payload_digest=str(c["payload_digest"]),
                payload=c.get("payload"),
                external_ref=c.get("external_ref"),
            )
            for c in raw.get("components") or []
        ],
    )


class CheckpointCoordinator:
    def __init__(
        self,
        *,
        agent_id: str,
        path: Path,
        participants: list[SnapshotParticipant],
        clock: Clock | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.path = Path(path)
        self.participants = list(participants)
        self.clock = clock or SystemClock()

    def checkpoint(self, logical_cycle: int) -> CheckpointEnvelope:
        components: list[CheckpointComponent] = []
        for participant in self.participants:
            payload = participant.snapshot()
            if not isinstance(payload, dict):
                raise TypeError(f"{participant.participant_id} snapshot must be a dict")
            reported = payload.get("agent_id")
            if reported is not None and str(reported) != self.agent_id:
                raise CheckpointIdentityError(
                    f"agent_id mismatch: envelope={self.agent_id!r} participant={reported!r}"
                )
            reported_cycle = payload.get("logical_cycle", payload.get("cycle"))
            if reported_cycle is not None and int(reported_cycle) != int(logical_cycle):
                raise CheckpointIdentityError(
                    f"cycle mismatch: envelope={logical_cycle} participant={reported_cycle}"
                )
            components.append(
                CheckpointComponent(
                    participant_id=participant.participant_id,
                    schema_version=participant.schema_version,
                    payload_digest=payload_digest(payload),
                    payload=payload,
                    external_ref=None,
                )
            )
        env = CheckpointEnvelope(
            checkpoint_version=CHECKPOINT_VERSION,
            checkpoint_id=new_id("ckpt"),
            agent_id=self.agent_id,
            logical_cycle=int(logical_cycle),
            created_at=self.clock.now(),
            components=components,
        )
        self._atomic_write(_envelope_to_json(env))
        return env

    def restore(self, *, expected_cycle: int | None = None, expected_agent_id: str | None = None) -> CheckpointEnvelope:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        env = _envelope_from_json(raw)
        if env.checkpoint_version != CHECKPOINT_VERSION:
            raise CheckpointIdentityError(
                f"checkpoint_version mismatch: expected={CHECKPOINT_VERSION!r} "
                f"envelope={env.checkpoint_version!r}"
            )
        want_agent = expected_agent_id if expected_agent_id is not None else self.agent_id
        if env.agent_id != want_agent:
            raise CheckpointIdentityError(
                f"agent_id mismatch: expected={want_agent!r} envelope={env.agent_id!r}"
            )
        if expected_cycle is not None and env.logical_cycle != int(expected_cycle):
            raise CheckpointIdentityError(
                f"cycle mismatch: expected={expected_cycle} envelope={env.logical_cycle}"
            )
        by_id = {p.participant_id: p for p in self.participants}
        component_ids = [component.participant_id for component in env.components]
        if len(component_ids) != len(set(component_ids)):
            raise CheckpointIdentityError("duplicate checkpoint participant")
        if set(component_ids) != set(by_id):
            raise CheckpointIdentityError("checkpoint participant set mismatch")
        verified: list[tuple[SnapshotParticipant, dict]] = []
        for component in env.components:
            payload = component.payload if component.payload is not None else {}
            digest = payload_digest(payload)
            if digest != component.payload_digest:
                raise DigestMismatchError(
                    f"digest mismatch for {component.participant_id}: "
                    f"stored={component.payload_digest} computed={digest}"
                )
            participant = by_id.get(component.participant_id)
            if participant is None:
                raise CheckpointIdentityError(f"unknown participant {component.participant_id!r}")
            if participant.schema_version != component.schema_version:
                raise CheckpointIdentityError(
                    f"schema_version mismatch for {component.participant_id}"
                )
            verified.append((participant, payload))
        for participant, payload in verified:
            participant.restore(payload)
        return env

    def _atomic_write(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
                encoding="utf-8",
            )
            tmp.replace(self.path)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
