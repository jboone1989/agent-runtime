"""Executable context for deliberation. Not extra MotivationEngine.process arguments."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..motivation.engine import MotivationState
from ..observations import Event
from ..planning.plan import Plan
from .working_context import WorkingContext


@dataclass
class ExecutiveContext:
    agent_id: str
    logical_cycle: int
    timestamp: datetime
    plan: Plan | None
    motivation: MotivationState
    event: Event | None
    working_context: WorkingContext
    usable_capability_ids: list[str]
