from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .state import RegulatoryState


@dataclass
class RegulationContext:
    environment: Any
    regulatory_state: RegulatoryState
    cycle: int
    body: Any = None
