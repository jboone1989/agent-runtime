"""Local inventory source. Usable bindings only; remote access stays REMOTE_ACCESS."""
from __future__ import annotations

from ..inventory import CapabilityInventory
from ..model import (
    CapabilityAccessMode,
    CapabilityBinding,
    CapabilityCandidate,
    CapabilityKind,
    CapabilityRequirement,
    SideEffectLevel,
)


class LocalCapabilitySource:
    source_id = "local"

    def __init__(self, inventory: CapabilityInventory) -> None:
        self.inventory = inventory

    def search(self, requirement: CapabilityRequirement) -> list[CapabilityCandidate]:
        if not self.inventory.usable(requirement.capability_id):
            return []
        entry = self.inventory.get(requirement.capability_id)
        if entry is None:
            return []
        return [_candidate_from_binding(entry)]


def _candidate_from_binding(binding: CapabilityBinding) -> CapabilityCandidate:
    return CapabilityCandidate(
        candidate_id=f"local:{binding.capability_id}",
        capability_id=binding.capability_id,
        kind=binding.kind,
        provider=binding.provider,
        source="local",
        license="MIT" if binding.kind == CapabilityKind.BUILTIN else None,
        side_effects=binding.side_effects,
        provenance={"access_mode": binding.access_mode.value},
        evidence_refs=list(binding.evidence_refs),
    )


def access_mode_of(candidate: CapabilityCandidate) -> str | None:
    raw = candidate.provenance.get("access_mode")
    return raw if isinstance(raw, str) else None


def is_owned_or_local(candidate: CapabilityCandidate) -> bool:
    mode = access_mode_of(candidate)
    return mode in {CapabilityAccessMode.OWNED.value, CapabilityAccessMode.LOCAL.value}


def is_remote_access(candidate: CapabilityCandidate) -> bool:
    return access_mode_of(candidate) == CapabilityAccessMode.REMOTE_ACCESS.value
