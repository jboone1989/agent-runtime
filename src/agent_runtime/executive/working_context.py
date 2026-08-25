"""Working context: what actually entered the executive window this tick."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ..experience.refs import stable_ref
from .attention import AttentionAllocation


@dataclass
class WorkingContext:
    context_id: str
    active_goal_ids: list[str]
    active_plan_id: str | None
    attended_signal_refs: list[str]
    retrieved_memory_refs: list[str]
    open_gap_ids: list[str] = field(default_factory=list)
    commitment_ids: list[str] = field(default_factory=list)
    agency_thread_ids: list[str] = field(default_factory=list)
    token_budget: int = 0
    compute_budget: float = 0.0


class WorkingContextBuilder:
    def build(
        self,
        *,
        agent_id: str,
        logical_cycle: int,
        timestamp: datetime,
        goal_ids: list[str],
        plan_id: str | None,
        attention: AttentionAllocation,
        token_budget: int = 0,
        compute_budget: float = 0.0,
        open_gap_ids: list[str] | None = None,
        commitment_ids: list[str] | None = None,
        agency_thread_ids: list[str] | None = None,
    ) -> WorkingContext:
        _ = timestamp
        return WorkingContext(
            context_id=stable_ref("ctx", agent_id, logical_cycle, plan_id),
            active_goal_ids=list(goal_ids),
            active_plan_id=plan_id,
            attended_signal_refs=[ref for ref in attention.attended if ref.startswith("sig:")],
            retrieved_memory_refs=[ref for ref in attention.attended if ref.startswith("mem:")],
            open_gap_ids=list(open_gap_ids or []),
            commitment_ids=list(commitment_ids or []),
            agency_thread_ids=list(agency_thread_ids or []),
            token_budget=token_budget,
            compute_budget=compute_budget,
        )
