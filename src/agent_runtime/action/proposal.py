from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..clock import new_id


@dataclass
class ActionProposal:
    kind: str
    resource: str
    compute_cost: float
    financial_cost: float
    goal_id: str | None
    reason: str
    payload: dict[str, Any] = field(default_factory=dict)
    trigger: str = "INTERNAL"
    goal_origin: str | None = None
    evidence: list[str] = field(default_factory=list)
    action_id: str = field(default_factory=lambda: new_id("act"))
