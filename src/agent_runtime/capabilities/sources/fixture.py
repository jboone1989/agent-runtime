"""Static/fake external source for tests. Does not network or install."""
from __future__ import annotations

from ..model import CapabilityCandidate, CapabilityRequirement


class FixtureCapabilitySource:
    source_id = "fixture"

    def __init__(self, candidates: list[CapabilityCandidate] | None = None) -> None:
        self._candidates = list(candidates or [])

    def search(self, requirement: CapabilityRequirement) -> list[CapabilityCandidate]:
        return [
            candidate
            for candidate in self._candidates
            if candidate.capability_id == requirement.capability_id
        ]
