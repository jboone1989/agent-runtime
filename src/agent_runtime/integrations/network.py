"""Optional network adapter protocol. Runtime must run with this unset.

Does not import any Agent Network package.
"""
from __future__ import annotations

from typing import Any, Protocol


class NetworkAdapter(Protocol):
    async def publish_version(self, *args: Any, **kwargs: Any) -> None: ...

    async def publish_evolution(self, *args: Any, **kwargs: Any) -> None: ...

    async def publish_lineage(self, *args: Any, **kwargs: Any) -> None: ...

    async def discover(self, *args: Any, **kwargs: Any) -> list[Any]: ...

    async def request_verification(self, *args: Any, **kwargs: Any) -> Any: ...

    async def get_reputation(self, *args: Any, **kwargs: Any) -> Any: ...


class NullNetworkAdapter:
    """Default: the social layer is not a runtime dependency."""

    async def publish_version(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def publish_evolution(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def publish_lineage(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def discover(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    async def request_verification(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def get_reputation(self, *args: Any, **kwargs: Any) -> None:
        return None
