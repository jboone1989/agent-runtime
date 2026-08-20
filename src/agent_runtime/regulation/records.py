from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class RegulationRecord:
    record_id: str
    cycle: int
    timestamp: datetime
    system: str
    reason: str
    local_actions: list[str]
    escalations: list[str]
    confidence: float
    denied: list[dict[str, Any]] = field(default_factory=list)
