"""Goals. Internal / viability goals are first-class — not just user tasks."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..clock import new_id


class GoalOrigin(str, Enum):
    USER = "USER"
    INTERNAL = "INTERNAL"
    VIABILITY = "VIABILITY"
    LEARNED = "LEARNED"
    OFFSPRING = "OFFSPRING"


class GoalStatus(str, Enum):
    PROPOSED = "PROPOSED"
    ACTIVE = "ACTIVE"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class Goal:
    goal_id: str
    description: str
    origin: GoalOrigin
    status: GoalStatus
    created_at: datetime
    reason: str
    priority: float = 0.5
    parent_id: str | None = None
    evidence_refs: list[str] = field(default_factory=list)
    success_criteria: str = ""
    dimension: str | None = None

    @classmethod
    def create(
        cls,
        *,
        description: str,
        origin: GoalOrigin,
        created_at: datetime,
        reason: str,
        priority: float = 0.5,
        evidence_refs: list[str] | None = None,
        dimension: str | None = None,
        success_criteria: str = "",
    ) -> Goal:
        return cls(
            goal_id=new_id("goal"),
            description=description,
            origin=origin,
            status=GoalStatus.ACTIVE,
            created_at=created_at,
            reason=reason,
            priority=priority,
            evidence_refs=evidence_refs or [],
            dimension=dimension,
            success_criteria=success_criteria,
        )
