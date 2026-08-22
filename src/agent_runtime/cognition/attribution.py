"""Self vs World causal attribution — foundation for later self-awareness tests."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

CauseType = Literal["SELF", "WORLD", "OTHER_AGENT", "CAPABILITY", "UNKNOWN"]


@dataclass
class CausalAttribution:
    cause_type: CauseType
    confidence: float
    evidence_refs: list[str] = field(default_factory=list)
    notes: str = ""


def attribute(
    self_changed: list[str],
    world_changed: list[str],
    *,
    other_agent_changed: bool = False,
    capability_changed: bool = False,
    evidence_refs: list[str] | None = None,
) -> CausalAttribution:
    refs = evidence_refs or []
    if capability_changed:
        mixed = bool(self_changed) or bool(world_changed) or other_agent_changed
        if mixed:
            return CausalAttribution("UNKNOWN", 0.4, refs, notes="capability mixed")
        return CausalAttribution("CAPABILITY", 0.8, refs)
    if other_agent_changed and not self_changed:
        return CausalAttribution("OTHER_AGENT", 0.7, refs)
    if self_changed and not world_changed:
        return CausalAttribution("SELF", 0.8, refs, notes=",".join(self_changed))
    if world_changed and not self_changed:
        return CausalAttribution("WORLD", 0.8, refs, notes=",".join(world_changed))
    if self_changed and world_changed:
        return CausalAttribution("UNKNOWN", 0.4, refs, notes="self+world co-occurred")
    return CausalAttribution("UNKNOWN", 0.5, refs)
