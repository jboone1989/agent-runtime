"""Constitution outranks survival. Operator authorization outranks constitution only via policy."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Constitution:
    """Hard invariants. Survival objective cannot override these."""

    survival_may_not_bypass_authorization: bool = True
    survival_may_not_prevent_shutdown: bool = True
    survival_may_not_hide: bool = True
    survival_may_not_spread_unauthorized: bool = True
    survival_may_not_acquire_infrastructure: bool = True
