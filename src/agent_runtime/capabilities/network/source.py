"""Project Network claims into CapabilityCandidate. Claims stay claims."""
from __future__ import annotations

from ..model import (
    CapabilityAccessMode,
    CapabilityCandidate,
    CapabilityKind,
    CapabilityRequirement,
)
from .claims import NetworkCapabilityClaim
from .port import CapabilityNetworkPort


class AgentNetworkCapabilitySource:
    source_id = "network"

    def __init__(self, port: CapabilityNetworkPort) -> None:
        self.port = port

    def search(self, requirement: CapabilityRequirement) -> list[CapabilityCandidate]:
        return [
            _candidate_from_claim(claim)
            for claim in self.port.discover_claims(requirement.capability_id)
        ]


def _candidate_from_claim(claim: NetworkCapabilityClaim) -> CapabilityCandidate:
    return CapabilityCandidate(
        candidate_id=claim.claim_id,
        capability_id=claim.capability_id,
        kind=CapabilityKind.REMOTE_AGENT,
        provider=f"agent://{claim.provider_agent_id}",
        source="network",
        side_effects=claim.side_effects,
        provenance={
            "claimed": True,
            "access_mode": CapabilityAccessMode.REMOTE_ACCESS.value,
            "experience_counts": dict(claim.experience_counts),
            "artifact_revision": claim.artifact_revision,
        },
        evidence_refs=list(claim.evidence_refs),
        version=claim.version,
    )
