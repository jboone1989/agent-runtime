"""Process-local motivation port. Protocol has evaluate only — no snapshot/restore."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MotivationPort(Protocol):
    def evaluate(self, request: dict) -> dict: ...


class InProcessMotivationPort:
    """Wraps any object that implements ``evaluate(dict) -> dict``.

    Does not import VirtualBrain. Restart tests talk to the session object
    directly; this port is not a lifecycle participant.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def evaluate(self, request: dict) -> dict:
        return self._inner.evaluate(request)
