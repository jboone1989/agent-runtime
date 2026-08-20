from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EnvironmentReader(Protocol):
    environment_id: str

    def read(self, channel: str) -> Any: ...

    def describe_capabilities(self) -> set[str]: ...


@runtime_checkable
class EnvironmentController(EnvironmentReader, Protocol):
    def apply(self, channel: str, value: Any) -> None: ...


# Historical name: full read/write adapter used by actuators and digital packs.
EnvironmentAdapter = EnvironmentController


class EnvironmentReadView:
    """Sensors may only read. Mutating apply is intentionally absent."""

    def __init__(self, inner: EnvironmentReader) -> None:
        self.environment_id = inner.environment_id
        self._inner = inner

    def read(self, channel: str) -> Any:
        return self._inner.read(channel)

    def describe_capabilities(self) -> set[str]:
        return self._inner.describe_capabilities()
