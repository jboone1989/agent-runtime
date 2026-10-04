"""Provider-neutral client for the reusable memory capability."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .protocol import CapabilityClient, CapabilityInvoker
from .standard import MEMORY_CAPABILITY_ID


class MemoryCapabilityClient(CapabilityClient):
    """Small stable surface; implementation-specific memory APIs stay behind providers."""

    def __init__(self, invoker: CapabilityInvoker) -> None:
        super().__init__(invoker, MEMORY_CAPABILITY_ID)

    def remember(self, record: Mapping[str, Any]) -> Mapping[str, Any]:
        return self.call("remember", {"record": dict(record)}).payload

    def recall(
        self,
        query: str,
        *,
        limit: int = 8,
        topic: str = "",
        node_types: tuple[str, ...] | list[str] = (),
        source_ids: tuple[str, ...] | list[str] = (),
    ) -> tuple[Mapping[str, Any], ...]:
        payload = self.call(
            "recall",
            {
                "query": query,
                "limit": int(limit),
                "topic": topic,
                "node_types": list(node_types),
                "source_ids": list(source_ids),
            },
        ).payload
        items = payload.get("items")
        if not isinstance(items, list) or any(not isinstance(item, Mapping) for item in items):
            raise TypeError("memory recall reply requires an items array")
        return tuple(dict(item) for item in items)

    def read(self, memory_id: str, *, offset: int = 0) -> Mapping[str, Any]:
        return self.call(
            "read",
            {"memory_id": memory_id, "offset": int(offset)},
        ).payload

    def changes(self, *, since: int = 0, limit: int = 8) -> tuple[Mapping[str, Any], ...]:
        payload = self.call(
            "changes",
            {"since": int(since), "limit": int(limit)},
        ).payload
        items = payload.get("items")
        if not isinstance(items, list) or any(not isinstance(item, Mapping) for item in items):
            raise TypeError("memory changes reply requires an items array")
        return tuple(dict(item) for item in items)

    def health(self) -> Mapping[str, Any]:
        return self.call("health", {}).payload


__all__ = ["MemoryCapabilityClient"]
