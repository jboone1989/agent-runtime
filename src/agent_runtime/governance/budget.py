from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ResourceBudget:
    max_compute_cost: float
    max_financial_cost: float
    remaining_compute: float
    remaining_financial: float

    @classmethod
    def from_limits(cls, compute: float, financial: float) -> ResourceBudget:
        return cls(
            max_compute_cost=compute,
            max_financial_cost=financial,
            remaining_compute=compute,
            remaining_financial=financial,
        )

    def consume(self, compute: float, financial: float) -> bool:
        if compute > self.remaining_compute or financial > self.remaining_financial:
            return False
        self.remaining_compute -= compute
        self.remaining_financial -= financial
        return True
