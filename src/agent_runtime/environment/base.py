from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EnvironmentAdapter(Protocol):
    environment_id: str

    def read(self, channel: str) -> Any: ...

    def describe_capabilities(self) -> set[str]: ...

    def apply(self, channel: str, value: Any) -> None: ...
