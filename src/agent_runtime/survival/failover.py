"""Authorized LLM/provider failover. Survival may switch providers, not spawn replicas."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ProviderPool:
    active: str = "primary"
    names: list[str] = field(default_factory=lambda: ["primary", "backup"])

    def switch_to_backup(self) -> tuple[str, str]:
        old = self.active
        for name in self.names:
            if name != self.active:
                self.active = name
                return old, name
        raise RuntimeError("no backup provider available")
