from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RegulatoryAction:
    kind: str
    reason: str
    resource: str = "self"
    payload: dict[str, Any] = field(default_factory=dict)
    compute_cost: float = 0.02
    financial_cost: float = 0.0
    trigger: str = "REGULATION"


@dataclass
class RegulatoryStateUpdate:
    path: str
    value: Any
    reason: str
