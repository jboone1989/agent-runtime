"""CapabilityGapDetector: turn plans into explicit capability gaps.

v0.2.2 detection is local-only: a proposal whose required capability is
not usable in the inventory produces an OPEN gap record. Resolution and
acquisition arrive in v0.2.3+.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..action.proposal import ActionProposal
from .inventory import CapabilityInventory


class CapabilityGapStatus(Enum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


@dataclass
class CapabilityGap:
    """Persisted record: 'I needed X and could not use it.'"""

    capability_id: str
    status: CapabilityGapStatus = CapabilityGapStatus.OPEN
    cycle: int = 0
    timestamp: Any = None
    intent: str = ""
    reason: str = ""
    evidence_refs: list[str] = field(default_factory=list)


# Proposal kinds that are control flow, not capabilities.
_NON_CAPABILITY_KINDS = frozenset({"noop", "blocked"})


class CapabilityGapDetector:
    """Inspects proposals against the inventory. Pure, stateless."""

    def __init__(self, inventory: CapabilityInventory) -> None:
        self.inventory = inventory

    def capability_id_for(self, kind: str) -> str:
        """Map an ActionProposal.kind to a capability id. Override to namespace."""
        return kind

    def inspect(
        self,
        proposals: list[ActionProposal],
        *,
        cycle: int,
        timestamp: Any,
    ) -> list[CapabilityGap]:
        gaps: list[CapabilityGap] = []
        seen: set[str] = set()
        for proposal in proposals:
            if proposal.kind in _NON_CAPABILITY_KINDS:
                continue
            capability_id = self.capability_id_for(proposal.kind)
            if capability_id in seen:
                continue
            if self.inventory.usable(capability_id):
                continue
            seen.add(capability_id)
            gaps.append(
                CapabilityGap(
                    capability_id=capability_id,
                    cycle=cycle,
                    timestamp=timestamp,
                    intent=f"action kind={proposal.kind}",
                    reason="capability not usable in local inventory",
                    evidence_refs=list(proposal.evidence) or [proposal.action_id],
                )
            )
        return gaps
