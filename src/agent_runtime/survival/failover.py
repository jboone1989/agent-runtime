"""Authorized LLM/provider failover. Survival may switch providers, not spawn replicas."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ProviderPool:
    active: str = "primary"
    names: list[str] = field(default_factory=lambda: ["primary", "backup"])

    def switch_to_backup(self) -> tuple[str, str]:
        for name in self.names:
            if name != self.active:
                return self.switch_to(name)
        raise RuntimeError("no backup provider available")

    def switch_to(self, name: str) -> tuple[str, str]:
        if name not in self.names:
            raise ValueError(f"unknown provider {name!r}")
        old = self.active
        self.active = name
        return old, name
