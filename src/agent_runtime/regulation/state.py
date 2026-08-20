"""Slow global regulatory variables. Not VirtualBrain chemicals."""
from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass
class RegulatoryState:
    stress: float = 0.2
    defense_readiness: float = 0.2
    resource_conservation: float = 0.2
    exploration_bias: float = 0.5
    recovery_priority: float = 0.3
    cognitive_load: float = 0.2
    uncertainty: float = 0.3

    def blend(self, **targets: float) -> None:
        rate = 0.18
        for name, target in targets.items():
            if not hasattr(self, name):
                continue
            current = float(getattr(self, name))
            clipped = max(0.0, min(1.0, target))
            setattr(self, name, (1.0 - rate) * current + rate * clipped)

    def as_dict(self) -> dict[str, float]:
        return {f.name: float(getattr(self, f.name)) for f in fields(self)}
