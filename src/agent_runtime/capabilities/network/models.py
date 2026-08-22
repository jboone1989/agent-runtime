"""Task / receipt records kept on the Runtime side of a Network projection."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TaskProposal:
    proposal_id: str
    capability_id: str
    provider_agent_id: str
    payload: dict[str, Any]
    idempotency_key: str
    irreversible: bool = False
    cycle: int = 0


@dataclass
class TaskContract:
    contract_id: str
    proposal_id: str
    capability_id: str
    provider_agent_id: str
    idempotency_key: str
    status: str
    artifact_revision: str = ""


@dataclass
class VerificationReceipt:
    receipt_id: str
    contract_id: str
    artifact_revision: str
    accepted: bool
    source: str
    verifier_id: str
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class DelegationResult:
    success: bool
    reason: str
    contract: TaskContract | None = None
    receipts: list[VerificationReceipt] = field(default_factory=list)
    fallback_candidate_id: str | None = None
    replan: bool = False
    evidence_refs: list[str] = field(default_factory=list)
    outcome_ref: str | None = None
