"""Interrupt assessment. High-severity unresolved threat may pause an ordinary plan."""
from __future__ import annotations

from ..motivation.engine import MotivationState
from ..observations import Event
from ..planning.plan import Plan
from ..planning.state import PlanStatus

HIGH_THREAT = 0.7
_PAUSABLE = {PlanStatus.ACTIVE, PlanStatus.WAITING}


def should_interrupt(
    *,
    plan: Plan | None,
    motivation: MotivationState,
    event: Event | None,
) -> bool:
    if plan is None or plan.status not in _PAUSABLE:
        return False
    if event is not None and event.data.get("resolved") is True:
        return False
    unresolved = event is not None and event.data.get("resolved") is False
    return unresolved and motivation.threat >= HIGH_THREAT
