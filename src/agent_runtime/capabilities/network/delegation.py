"""Delegate a task to a remote agent. Trust counts do not authorize."""
from __future__ import annotations

from ...experience.refs import stable_ref
from ..inventory import CapabilityInventory
from ..model import (
    CapabilityAccessMode,
    CapabilityBinding,
    CapabilityKind,
    CapabilityLifecycleState,
)
from .claims import NetworkCapabilityClaim
from .models import DelegationResult, TaskProposal, VerificationReceipt
from .port import CapabilityNetworkPort, NullCapabilityNetworkPort


class DelegationService:
    def __init__(self, port: CapabilityNetworkPort | None = None) -> None:
        self.port = port or NullCapabilityNetworkPort()

    def accept(
        self,
        claim: NetworkCapabilityClaim,
        *,
        receipts: list[VerificationReceipt],
        quorum: int,
        expected_contract_id: str | None = None,
    ) -> DelegationResult:
        if not isinstance(quorum, int) or isinstance(quorum, bool) or quorum < 1:
            return DelegationResult(success=False, reason="quorum must be a positive integer")
        if not claim.artifact_revision:
            return DelegationResult(success=False, reason="artifact revision is required")
        matching_by_verifier: dict[str, VerificationReceipt] = {}
        for receipt in receipts:
            if not receipt.accepted:
                continue
            if receipt.artifact_revision != claim.artifact_revision:
                continue
            if expected_contract_id is not None and receipt.contract_id != expected_contract_id:
                continue
            if not receipt.verifier_id:
                continue
            matching_by_verifier.setdefault(receipt.verifier_id, receipt)
        matching = list(matching_by_verifier.values())
        if len(matching) < quorum:
            reason = "quorum not met"
            if receipts and not any(
                receipt.accepted and receipt.artifact_revision == claim.artifact_revision
                for receipt in receipts
            ):
                reason = "artifact revision mismatch"
            elif expected_contract_id is not None and receipts and not any(
                receipt.contract_id == expected_contract_id for receipt in receipts
            ):
                reason = "contract mismatch"
            return DelegationResult(success=False, reason=reason, receipts=list(receipts))
        return DelegationResult(
            success=True,
            reason="quorum met",
            receipts=matching,
            evidence_refs=_evidence(claim, matching),
        )

    def delegate(
        self,
        claim: NetworkCapabilityClaim,
        *,
        task_payload: dict,
        authorized: bool,
        cycle: int,
        alternatives: list[NetworkCapabilityClaim] | None = None,
        quorum: int = 1,
        irreversible: bool = False,
        idempotency_key: str | None = None,
        inventory: CapabilityInventory | None = None,
        private: dict | None = None,
    ) -> DelegationResult:
        _ = private
        if not authorized:
            return DelegationResult(success=False, reason="not authorized", replan=True)
        key = idempotency_key or stable_ref("idk", claim.capability_id, cycle)
        last = DelegationResult(success=False, reason="delegation failed", replan=True)
        for index, candidate in enumerate([claim, *(alternatives or [])]):
            proposal = TaskProposal(
                proposal_id=stable_ref("ntp", candidate.claim_id, cycle, index),
                capability_id=candidate.capability_id,
                provider_agent_id=candidate.provider_agent_id,
                payload=dict(task_payload),
                idempotency_key=key,
                artifact_revision=candidate.artifact_revision,
                irreversible=irreversible,
                cycle=cycle,
            )
            contract = self.port.submit_task(proposal)
            if contract.status != "accepted":
                last = DelegationResult(
                    success=False,
                    reason="delegation failed",
                    contract=contract,
                    replan=True,
                )
                continue
            receipts = list(self.port.receipts_for(contract))
            accepted = self.accept(
                candidate,
                receipts=receipts,
                quorum=quorum,
                expected_contract_id=contract.contract_id,
            )
            if not accepted.success:
                last = DelegationResult(
                    success=False,
                    reason=accepted.reason,
                    contract=contract,
                    receipts=receipts,
                    replan=True,
                )
                continue
            if inventory is not None:
                _bind_remote(inventory, candidate)
            return DelegationResult(
                success=True,
                reason="delegated",
                contract=contract,
                receipts=accepted.receipts,
                fallback_candidate_id=None if index == 0 else candidate.claim_id,
                replan=False,
                evidence_refs=_evidence(candidate, accepted.receipts),
                outcome_ref=None if contract is None else contract.contract_id,
            )
        return last


def _bind_remote(inventory: CapabilityInventory, claim: NetworkCapabilityClaim) -> None:
    inventory.register(
        CapabilityBinding(
            capability_id=claim.capability_id,
            kind=CapabilityKind.REMOTE_AGENT,
            provider=f"agent://{claim.provider_agent_id}",
            access_mode=CapabilityAccessMode.REMOTE_ACCESS,
            state=CapabilityLifecycleState.ACTIVE,
            version=claim.version,
            evidence_refs=list(claim.evidence_refs),
        )
    )


def _evidence(
    claim: NetworkCapabilityClaim, receipts: list[VerificationReceipt]
) -> list[str]:
    refs = list(claim.evidence_refs)
    for receipt in receipts:
        refs.extend(receipt.evidence_refs)
        refs.append(f"network:{receipt.receipt_id}")
    return refs
