"""Search evidence required before CREATE. Recording is not acquisition."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CapabilitySearchRecord:
    search_id: str
    capability_id: str
    plan_step_id: str | None
    sources_consulted: list[str]
    candidate_ids: list[str]
    rejected: list[dict]
    cycle: int
    evidence_refs: list[str] = field(default_factory=list)
