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
    timestamp: Any = None
    success: bool = False
    latency_ms: float | None = None
    goal_id: str | None = None
    action_id: str | None = None
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
