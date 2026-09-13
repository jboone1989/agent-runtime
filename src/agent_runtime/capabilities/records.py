"""Usage and acquisition records — the evidence Learning will consume in v0.3.

Records capture observable history only. They are never scores:
'Twitter provider A succeeded 98% of the time' must stay a countable
fact, not a black-box trust value.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .model import CapabilityDecisionAction, CapabilityLifecycleState


@dataclass
class CapabilityUsageRecord:
    """One concrete use of a capability binding."""

    capability_id: str
    cycle: int
    binding_id: str | None = None
    candidate_id: str | None = None
    plan_step_id: str | None = None
    attempt_id: str | None = None
    timestamp: Any = None
    success: bool = False
    evidence_result: str | None = None
    latency_ms: float | None = None
    goal_id: str | None = None
    action_id: str | None = None
    evidence_refs: list[str] = field(default_factory=list)
    cost: float | None = None
    side_effects: list[str] = field(default_factory=list)
    failure_class: str | None = None


@dataclass
class CapabilityHistoryRecord:
    """Compact causal history retained after a binding leaves the inventory."""

    capability_id: str
    binding_id: str | None
    candidate_id: str | None
    provider: str
    active_from_cycle: int | None = None
    retired_at_cycle: int | None = None
    why_acquired: str = ""
    why_retired: str = ""
    verified_usage_count: int = 0
    successful_usage_count: int = 0
    replacement_reference: str | None = None
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class CapabilityAcquisitionRecord:
    """How a capability entered (or failed to enter) the inventory."""

    capability_id: str
    candidate_id: str
    source: str
    action: CapabilityDecisionAction
    cycle: int
    timestamp: Any = None
    outcome_state: CapabilityLifecycleState = CapabilityLifecycleState.DISCOVERED
    operator_approved: bool = False
    evidence_refs: list[str] = field(default_factory=list)
