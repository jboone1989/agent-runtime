"""ExecutiveDecision record. Deterministic given the same ExecutiveContext."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ExecutiveDecision:
    decision_id: str
    continue_plan: bool
    interrupt_plan: bool
    request_replan: bool
    selected_goal_ids: list[str]
    selected_step_id: str | None
    reasons: list[str]
    evidence_refs: list[str] = field(default_factory=list)
    logical_cycle: int = 0
    timestamp: datetime | None = None
