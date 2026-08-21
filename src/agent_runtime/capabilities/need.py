"""CapabilityNeed: 'I am missing X, and here is why it matters now'."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .model import CapabilityRequirement, SideEffectLevel


@dataclass
class CapabilityNeed:
    """A requirement plus urgency and evidence — the motivation-facing form.

    Needs are what VirtualBrain-side capability awareness (SA-03+/SA-06+)
    will eventually read; the Runtime only records and evaluates them.
    """

    requirement: CapabilityRequirement
    urgency: float = 0.5
    evidence_refs: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def capability_id(self) -> str:
        return self.requirement.capability_id

    @classmethod
    def simple(
        cls,
        capability_id: str,
        *,
        intent: str = "",
        urgency: float = 0.5,
        evidence_refs: list[str] | None = None,
        max_side_effect: SideEffectLevel = SideEffectLevel.READ_ONLY,
    ) -> CapabilityNeed:
        return cls(
            requirement=CapabilityRequirement(
                capability_id=capability_id,
                intent=intent,
                max_side_effect=max_side_effect,
            ),
            urgency=urgency,
            evidence_refs=list(evidence_refs or []),
        )
