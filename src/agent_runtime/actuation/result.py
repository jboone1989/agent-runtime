from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ActuationResult:
    command_id: str
    success: bool
    description: str
    effects: dict[str, Any] = field(default_factory=dict)
    evidence_result: str | None = None
    missing_evidence_units: list[str] = field(default_factory=list)
