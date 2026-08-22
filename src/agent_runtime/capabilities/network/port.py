"""Sync capability-network port. Does not import the social-layer package."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .claims import NetworkCapabilityClaim
from .models import TaskContract, TaskProposal, VerificationReceipt


@runtime_checkable
class CapabilityNetworkPort(Protocol):
    def discover_claims(self, capability_id: str) -> list[NetworkCapabilityClaim]: ...

    def submit_task(self, proposal: TaskProposal) -> TaskContract: ...

    def receipts_for(self, contract: TaskContract) -> list[VerificationReceipt]: ...


class NullCapabilityNetworkPort:
    """Default: Runtime runs with no social layer attached."""

    def discover_claims(self, capability_id: str) -> list[NetworkCapabilityClaim]:
        _ = capability_id
        return []

    def submit_task(self, proposal: TaskProposal) -> TaskContract:
        return TaskContract(
            contract_id="null",
            proposal_id=proposal.proposal_id,
            capability_id=proposal.capability_id,
            provider_agent_id=proposal.provider_agent_id,
            idempotency_key=proposal.idempotency_key,
            status="unavailable",
        )

    def receipts_for(self, contract: TaskContract) -> list[VerificationReceipt]:
        _ = contract
        return []
