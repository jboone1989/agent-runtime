"""ExperienceTrace v1: record Goal → Plan → Step → Decision → Action → Outcome.

Recording is not learning. v0.3 consumes traces.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ExperienceTrace:
    trace_id: str
    agent_id: str
    logical_cycle: int
    observed_at: datetime
    effective_at: datetime
    goal_refs: list[str]
    plan_ref: str | None
    step_ref: str | None
    decision_ref: str | None
    action_refs: list[str]
    outcome_refs: list[str]
    caused_by: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    confidence: float = 1.0
