"""Acquisition / verification records. Discover != trusted. Acquired != active."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..model import CapabilityLifecycleState


@dataclass
class SandboxProfile:
    profile_id: str
    allow_network: bool = False
    allow_home: bool = False
    allow_subprocess: bool = False


@dataclass
class VerificationProbe:
    probe_id: str
    kind: str
    expected_digest: str
    expected_version: str | None = None


@dataclass
class VerificationResult:
    probe_id: str
    passed: bool
    digest: str
    version: str | None
    reasons: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class AcquisitionPlan:
    plan_id: str
    capability_id: str
    candidate_id: str
    steps: list[str]
    sandbox: SandboxProfile
    decision_ref: str | None = None


@dataclass
class AcquisitionTransaction:
    transaction_id: str
    plan_id: str
    capability_id: str
    candidate_id: str
    state: CapabilityLifecycleState
    content_digest: str
    version: str | None = None
    bound_version: str | None = None
    operator_approved: bool = False
    cycle: int = 0
    failure_reason: str = ""
    activation_id: str | None = None
    artifact: dict[str, Any] = field(default_factory=dict)
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class ActivationRecord:
    activation_id: str
    transaction_id: str
    capability_id: str
    digest: str
    version: str | None
    cycle: int
    active: bool = True


@dataclass
class RollbackRecord:
    rollback_id: str
    activation_id: str
    capability_id: str
    previous_state: CapabilityLifecycleState
    cycle: int
    reason: str
