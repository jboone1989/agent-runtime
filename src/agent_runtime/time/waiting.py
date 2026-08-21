"""Waiting conditions. Interrupt pauses a plan without failing its goal."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class WaitingState:
    wait_id: str
    plan_id: str
    reason: str
    until: str
    created_cycle: int
    evidence_refs: list[str] = field(default_factory=list)
