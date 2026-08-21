"""Future intentions. v0.2.3 records them; it does not schedule an LLM."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FutureIntention:
    intention_id: str
    description: str
    wait_until: str
    plan_id: str | None = None
    evidence_refs: list[str] = field(default_factory=list)
