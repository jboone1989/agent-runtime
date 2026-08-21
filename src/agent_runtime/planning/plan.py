"""Long-lived plan. Survives ticks and restart. Planner does not authorize."""
from __future__ import annotations

from dataclasses import dataclass, field

from .state import PlanStatus
from .step import PlanStep


@dataclass
class Plan:
    plan_id: str
    goal_ids: list[str]
    steps: list[PlanStep]
    current_step_id: str | None
    status: PlanStatus
    created_cycle: int
    evidence_refs: list[str] = field(default_factory=list)
    caused_by: list[str] = field(default_factory=list)

    @property
    def current_step(self) -> PlanStep | None:
        if self.current_step_id is None:
            return self.steps[0] if self.steps else None
        for step in self.steps:
            if step.step_id == self.current_step_id:
                return step
        return self.steps[0] if self.steps else None
