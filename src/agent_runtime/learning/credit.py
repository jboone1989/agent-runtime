"""Delayed outcome credit. Recording a trace is not itself a lesson."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CreditAssignment:
    outcome_id: str
    action_id: str
    trace_id: str
    plan_ref: str | None
    step_ref: str | None
    delayed: bool
