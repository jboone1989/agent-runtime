"""Digital environment — one EnvironmentAdapter, not the runtime core."""
from __future__ import annotations

from typing import Any

from ..sensing.world import SenseWorld
from ..survival.failover import ProviderPool
from .adapter import assign, walk


class DigitalEnvironmentAdapter:
    environment_id = "digital"

    def __init__(
        self,
        world: SenseWorld | None = None,
        pool: ProviderPool | None = None,
    ) -> None:
        self.world = world or SenseWorld()
        self.pool = pool or ProviderPool()

    def describe_capabilities(self) -> set[str]:
        return {"resources", "process", "providers", "memory", "security", "network"}

    def _tree(self) -> dict[str, Any]:
        return {
            "resources": self.world.resources,
            "process": self.world.process,
            "providers": self.world.providers,
            "memory": self.world.memory,
            "security": self.world.security,
            "network": self.world.network,
            "active_provider": self.pool.active,
        }

    def read(self, channel: str) -> Any:
        if channel == "active_provider":
            return self.pool.active
        return walk(self._tree(), channel)

    def apply(self, channel: str, value: Any) -> None:
        if channel == "active_provider":
            self.pool.active = str(value)
            return
        # Mutate nested SenseWorld dicts in place.
        parts = channel.split("/")
        if parts[0] == "providers" and len(parts) >= 2:
            name = parts[1]
            bucket = self.world.providers.setdefault(name, {})
            if len(parts) == 2 and isinstance(value, dict):
                bucket.update(value)
            elif len(parts) >= 3:
                assign(bucket, "/".join(parts[2:]), value)
            return
        mapping = {
            "resources": self.world.resources,
            "process": self.world.process,
            "memory": self.world.memory,
            "security": self.world.security,
            "network": self.world.network,
        }
        head = parts[0]
        if head not in mapping:
            return
        if len(parts) == 1 and isinstance(value, dict):
            mapping[head].update(value)
            return
        assign(mapping[head], "/".join(parts[1:]), value)
