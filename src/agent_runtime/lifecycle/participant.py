"""SnapshotParticipant protocol. Runtime does not interpret mind payloads."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SnapshotParticipant(Protocol):
    participant_id: str
    schema_version: str

    def snapshot(self) -> dict: ...

    def restore(self, payload: dict) -> None: ...


class OpaqueParticipant:
    """Adapts any object with snapshot/restore. Does not import VirtualBrain."""

    def __init__(
        self,
        inner: Any,
        *,
        participant_id: str | None = None,
        schema_version: str | None = None,
    ) -> None:
        self._inner = inner
        self.participant_id = participant_id or str(getattr(inner, "participant_id", "mind"))
        self.schema_version = schema_version or str(getattr(inner, "schema_version", "1"))

    def snapshot(self) -> dict:
        return self._inner.snapshot()

    def restore(self, payload: dict) -> None:
        self._inner.restore(payload)
