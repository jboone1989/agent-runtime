"""Capability requirements on PlanStep. Resolver (v0.2.4) consumes these; this slice only records them."""
from __future__ import annotations

from ..capabilities.model import CapabilityRequirement, SideEffectLevel


def requirement(
    capability_id: str,
    intent: str = "",
    *,
    max_side_effect: SideEffectLevel = SideEffectLevel.READ_ONLY,
) -> CapabilityRequirement:
    return CapabilityRequirement(
        capability_id=capability_id,
        intent=intent,
        max_side_effect=max_side_effect,
    )
