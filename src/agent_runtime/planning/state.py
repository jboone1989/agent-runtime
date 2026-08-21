"""Plan and step lifecycle. Distinct from GoalStatus."""
from __future__ import annotations

from enum import Enum


class PlanStatus(str, Enum):
    PROPOSED = "PROPOSED"
    ACTIVE = "ACTIVE"
    WAITING = "WAITING"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
