"""CapabilitySource protocol. v0.2.4 does not network."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..model import CapabilityCandidate, CapabilityRequirement


@runtime_checkable
class CapabilitySource(Protocol):
    source_id: str

    def search(self, requirement: CapabilityRequirement) -> list[CapabilityCandidate]: ...
