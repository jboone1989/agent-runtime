"""Network capability claims. A claim is not a fact and not an authorization."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..model import SideEffectLevel


@dataclass
class NetworkCapabilityClaim:
    claim_id: str
    capability_id: str
    provider_agent_id: str
    artifact_revision: str
    experience_counts: dict[str, int] = field(default_factory=dict)
    evidence_refs: list[str] = field(default_factory=list)
    version: str | None = None
    side_effects: SideEffectLevel = SideEffectLevel.READ_ONLY
    claimed: bool = True
