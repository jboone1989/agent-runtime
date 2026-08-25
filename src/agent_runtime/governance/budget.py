from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


@dataclass
class ResourceBudget:
    max_compute_cost: float
    max_financial_cost: float
    remaining_compute: float
    remaining_financial: float

    @classmethod
    def from_limits(cls, compute: float, financial: float) -> ResourceBudget:
        _validate_cost(compute, "compute limit")
        _validate_cost(financial, "financial limit")
        return cls(
            max_compute_cost=compute,
            max_financial_cost=financial,
            remaining_compute=compute,
            remaining_financial=financial,
        )

    def consume(self, compute: float, financial: float) -> bool:
        if not _valid_cost(compute) or not _valid_cost(financial):
            return False
        if not _valid_cost(self.remaining_compute) or not _valid_cost(
            self.remaining_financial
        ):
            return False
        if compute > self.remaining_compute or financial > self.remaining_financial:
            return False
        self.remaining_compute -= compute
        self.remaining_financial -= financial
        return True


def _valid_cost(value: float) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and isfinite(value)
        and value >= 0.0
    )


def _validate_cost(value: float, label: str) -> None:
    if not _valid_cost(value):
        raise ValueError(f"{label} must be a finite non-negative number")
