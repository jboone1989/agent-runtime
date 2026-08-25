"""In-memory social-layer stand-in for tests. No SDK import."""
from __future__ import annotations

from ...experience.refs import stable_ref
from .claims import NetworkCapabilityClaim
from .models import TaskContract, TaskProposal, VerificationReceipt


class FixtureNetworkPort:
    def __init__(self) -> None:
        self.claims: list[NetworkCapabilityClaim] = []
        self.fail_providers: set[str] = set()
        self.submitted: list[dict] = []
        self._contracts: dict[str, TaskContract] = {}

    def discover_claims(self, capability_id: str) -> list[NetworkCapabilityClaim]:
        return [claim for claim in self.claims if claim.capability_id == capability_id]

    def submit_task(self, proposal: TaskProposal) -> TaskContract:
        if proposal.irreversible and proposal.idempotency_key in self._contracts:
            return self._contracts[proposal.idempotency_key]
        self.submitted.append(
            {
                "provider": proposal.provider_agent_id,
                "payload": dict(proposal.payload),
                "idempotency_key": proposal.idempotency_key,
            }
        )
        status = "failed" if proposal.provider_agent_id in self.fail_providers else "accepted"
        contract = TaskContract(
            contract_id=stable_ref("nct", proposal.provider_agent_id, proposal.idempotency_key),
            proposal_id=proposal.proposal_id,
            capability_id=proposal.capability_id,
            provider_agent_id=proposal.provider_agent_id,
            idempotency_key=proposal.idempotency_key,
            status=status,
            artifact_revision=proposal.artifact_revision,
        )
        if proposal.irreversible and status == "accepted":
            self._contracts[proposal.idempotency_key] = contract
        return contract

    def receipts_for(self, contract: TaskContract) -> list[VerificationReceipt]:
        if contract.status != "accepted":
            return []
        return [
            VerificationReceipt(
                receipt_id=stable_ref("nrc", contract.contract_id),
                contract_id=contract.contract_id,
                artifact_revision=contract.artifact_revision,
                accepted=True,
                source="network",
                verifier_id="fixture-verifier",
                evidence_refs=[f"network:{contract.contract_id}"],
            )
        ]
