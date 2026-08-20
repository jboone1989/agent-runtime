from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Escalation:
    code: str
    severity: float
    reason: str
    evidence_refs: list[str] = field(default_factory=list)
    suggested_dimension: str | None = None
