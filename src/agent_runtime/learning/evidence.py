"""Verified v0.3 evidence derived from causal traces and recorded outcomes."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class VerifiedLearningEvidence:
    evidence_id: str
    agent_id: str
    trace_id: str
    outcome_id: str
    action_id: str
    goal_refs: list[str]
    plan_ref: str | None
    step_ref: str | None
    success: bool
    delayed: bool
    verified_at: datetime
    confidence: float
    evidence_refs: list[str] = field(default_factory=list)
