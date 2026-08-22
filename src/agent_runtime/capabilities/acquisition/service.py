"""AcquisitionService: inspect → policy → sandbox → verify → activate.

Does not call pip, bash, GitHub, MCP, or Agent Network.
Failed acquisition must not clobber an existing usable binding.
"""
from __future__ import annotations

from ...experience.refs import stable_ref
from ..inventory import CapabilityInventory
from ..model import (
    CapabilityAccessMode,
    CapabilityBinding,
    CapabilityCandidate,
    CapabilityKind,
    CapabilityLifecycleState,
    SideEffectLevel,
)
from ..policy import CapabilityPolicy
from .adapter import FixtureSkillAdapter
from .inspector import inspect_candidate
from .models import (
    AcquisitionPlan,
    AcquisitionTransaction,
    ActivationRecord,
    RollbackRecord,
    SandboxProfile,
    VerificationProbe,
    VerificationResult,
)

_PIPELINE = ("inspect", "policy", "sandbox", "probe", "verify", "activate")
_PROTECTED = {
    CapabilityLifecycleState.ACTIVE,
    CapabilityLifecycleState.DEGRADED,
}


class AcquisitionService:
    def __init__(
        self,
        policy: CapabilityPolicy | None = None,
        adapter: FixtureSkillAdapter | None = None,
    ) -> None:
        self.policy = policy or CapabilityPolicy()
        self.adapter = adapter or FixtureSkillAdapter()
        self.activation_records: list[ActivationRecord] = []
        self.rollback_records: list[RollbackRecord] = []

    def run(
        self,
        candidate: CapabilityCandidate,
        *,
        inventory: CapabilityInventory,
        cycle: int,
        operator_approved: bool = False,
    ) -> AcquisitionTransaction:
        inspection = inspect_candidate(candidate)
        if inspection.unsafe or inspection.unsupported:
            return self._reject(
                candidate,
                inventory=inventory,
                cycle=cycle,
                operator_approved=operator_approved,
                reason="; ".join(inspection.reasons) or "rejected",
                quarantine=inspection.unsafe and not inspection.unsupported,
            )
        if self._blocked_by_policy(candidate, operator_approved):
            return self._transaction(
                candidate,
                state=CapabilityLifecycleState.INSPECTED,
                cycle=cycle,
                operator_approved=operator_approved,
                failure_reason="approval required",
            )
        tx = self.install(
            candidate, inventory=inventory, cycle=cycle, operator_approved=operator_approved
        )
        if tx.state != CapabilityLifecycleState.ACQUIRED:
            return tx
        tx = self.verify(tx, inventory=inventory, cycle=cycle)
        if tx.state != CapabilityLifecycleState.VERIFIED:
            return tx
        return self.activate(tx, inventory=inventory, cycle=cycle)

    def install(
        self,
        candidate: CapabilityCandidate,
        *,
        inventory: CapabilityInventory,
        cycle: int,
        operator_approved: bool = False,
    ) -> AcquisitionTransaction:
        inspection = inspect_candidate(candidate)
        if inspection.unsafe or inspection.unsupported:
            return self._reject(
                candidate,
                inventory=inventory,
                cycle=cycle,
                operator_approved=operator_approved,
                reason="; ".join(inspection.reasons) or "rejected",
                quarantine=inspection.unsafe and not inspection.unsupported,
            )
        if self._blocked_by_policy(candidate, operator_approved):
            return self._transaction(
                candidate,
                state=CapabilityLifecycleState.INSPECTED,
                cycle=cycle,
                operator_approved=operator_approved,
                failure_reason="approval required",
            )
        sandbox = SandboxProfile(profile_id="fixture-deny-all")
        artifact = self.adapter.materialize(candidate, sandbox)
        digest = self.adapter.digest_of(artifact, candidate.version)
        tx = self._transaction(
            candidate,
            state=CapabilityLifecycleState.ACQUIRED,
            cycle=cycle,
            operator_approved=operator_approved,
            artifact=artifact,
            content_digest=digest,
            version=candidate.version,
            bound_version=candidate.version,
        )
        if _protected_binding(inventory, candidate.capability_id) is not None:
            tx.failure_reason = "existing usable binding preserved"
            tx.state = CapabilityLifecycleState.ACQUIRED
            return tx
        inventory.register(
            CapabilityBinding(
                capability_id=candidate.capability_id,
                kind=candidate.kind,
                provider=candidate.provider,
                access_mode=CapabilityAccessMode.LOCAL,
                state=CapabilityLifecycleState.ACQUIRED,
                version=candidate.version,
                entry_point={"digest": digest, "version": candidate.version},
                evidence_refs=[tx.transaction_id],
            )
        )
        return tx

    def verify(
        self,
        tx: AcquisitionTransaction,
        *,
        inventory: CapabilityInventory,
        cycle: int,
    ) -> AcquisitionTransaction:
        _ = cycle
        expected = tx.content_digest
        actual = self.adapter.digest_of(tx.artifact, tx.version)
        probe = VerificationProbe(
            probe_id=stable_ref("prb", tx.transaction_id),
            kind="digest",
            expected_digest=expected,
            expected_version=tx.bound_version,
        )
        reasons: list[str] = []
        if tx.version != tx.bound_version:
            reasons.append("version mismatch")
        if actual != expected:
            reasons.append("digest mismatch")
        result = VerificationResult(
            probe_id=probe.probe_id,
            passed=not reasons,
            digest=actual,
            version=tx.version,
            reasons=reasons,
            evidence_refs=[tx.transaction_id],
        )
        if not result.passed:
            tx.state = CapabilityLifecycleState.ACQUIRED
            tx.failure_reason = "; ".join(reasons)
            return tx
        tx.state = CapabilityLifecycleState.VERIFIED
        tx.failure_reason = ""
        binding = inventory.get(tx.capability_id)
        if binding is not None and _protected_binding(inventory, tx.capability_id) is None:
            binding.state = CapabilityLifecycleState.VERIFIED
        return tx

    def activate(
        self,
        tx: AcquisitionTransaction,
        *,
        inventory: CapabilityInventory,
        cycle: int,
    ) -> AcquisitionTransaction:
        if tx.state != CapabilityLifecycleState.VERIFIED:
            tx.failure_reason = tx.failure_reason or "not verified"
            return tx
        existing = _protected_binding(inventory, tx.capability_id)
        if existing is not None:
            tx.failure_reason = "existing usable binding preserved"
            return tx
        activation_id = stable_ref("actv", tx.transaction_id, cycle)
        inventory.register(
            CapabilityBinding(
                capability_id=tx.capability_id,
                kind=CapabilityKind.SKILL,
                provider=str(tx.artifact.get("provider") or "fixture"),
                access_mode=CapabilityAccessMode.LOCAL,
                state=CapabilityLifecycleState.ACTIVE,
                version=tx.bound_version,
                entry_point={"digest": tx.content_digest, "version": tx.bound_version},
                evidence_refs=[tx.transaction_id, activation_id],
            )
        )
        kind = tx.artifact.get("kind")
        if kind:
            binding = inventory.get(tx.capability_id)
            if binding is not None:
                try:
                    binding.kind = CapabilityKind(kind)
                except ValueError:
                    pass
        tx.state = CapabilityLifecycleState.ACTIVE
        tx.activation_id = activation_id
        self.activation_records.append(
            ActivationRecord(
                activation_id=activation_id,
                transaction_id=tx.transaction_id,
                capability_id=tx.capability_id,
                digest=tx.content_digest,
                version=tx.bound_version,
                cycle=cycle,
                active=True,
            )
        )
        return tx

    def rollback(
        self,
        tx: AcquisitionTransaction,
        *,
        inventory: CapabilityInventory,
        cycle: int,
        reason: str,
    ) -> RollbackRecord:
        previous = inventory.get(tx.capability_id)
        previous_state = (
            previous.state if previous is not None else CapabilityLifecycleState.DISCOVERED
        )
        activation_id = tx.activation_id or stable_ref("actv", tx.transaction_id)
        inventory.set_state(tx.capability_id, CapabilityLifecycleState.RETIRED)
        record = RollbackRecord(
            rollback_id=stable_ref("rbk", activation_id, cycle),
            activation_id=activation_id,
            capability_id=tx.capability_id,
            previous_state=previous_state,
            cycle=cycle,
            reason=reason,
        )
        self.rollback_records.append(record)
        for item in self.activation_records:
            if item.activation_id == activation_id:
                item.active = False
        tx.state = CapabilityLifecycleState.RETIRED
        return record

    def _blocked_by_policy(self, candidate: CapabilityCandidate, operator_approved: bool) -> bool:
        if operator_approved:
            return False
        if candidate.side_effects.value >= SideEffectLevel.EXTERNAL_SIDE_EFFECT.value:
            return True
        if candidate.acquisition_mode in {"system_package", "deployment"}:
            return True
        if "secret_access" in candidate.permissions:
            return True
        if not self.policy.may_auto_acquire(candidate.side_effects):
            return True
        return False

    def _reject(
        self,
        candidate: CapabilityCandidate,
        *,
        inventory: CapabilityInventory,
        cycle: int,
        operator_approved: bool,
        reason: str,
        quarantine: bool,
    ) -> AcquisitionTransaction:
        state = (
            CapabilityLifecycleState.QUARANTINED
            if quarantine
            else CapabilityLifecycleState.DISCOVERED
        )
        tx = self._transaction(
            candidate,
            state=state,
            cycle=cycle,
            operator_approved=operator_approved,
            failure_reason=reason,
        )
        if _protected_binding(inventory, candidate.capability_id) is not None:
            return tx
        if quarantine:
            inventory.register(
                CapabilityBinding(
                    capability_id=candidate.capability_id,
                    kind=candidate.kind,
                    provider=candidate.provider,
                    access_mode=CapabilityAccessMode.UNAVAILABLE,
                    state=CapabilityLifecycleState.QUARANTINED,
                    version=candidate.version,
                    evidence_refs=[tx.transaction_id],
                )
            )
        return tx

    def _transaction(
        self,
        candidate: CapabilityCandidate,
        *,
        state: CapabilityLifecycleState,
        cycle: int,
        operator_approved: bool,
        failure_reason: str = "",
        artifact: dict | None = None,
        content_digest: str = "",
        version: str | None = None,
        bound_version: str | None = None,
    ) -> AcquisitionTransaction:
        plan = AcquisitionPlan(
            plan_id=stable_ref("acq", candidate.capability_id, candidate.candidate_id, cycle),
            capability_id=candidate.capability_id,
            candidate_id=candidate.candidate_id,
            steps=list(_PIPELINE),
            sandbox=SandboxProfile(profile_id="fixture-deny-all"),
        )
        return AcquisitionTransaction(
            transaction_id=stable_ref("atx", plan.plan_id, state.value),
            plan_id=plan.plan_id,
            capability_id=candidate.capability_id,
            candidate_id=candidate.candidate_id,
            state=state,
            content_digest=content_digest,
            version=version if version is not None else candidate.version,
            bound_version=bound_version if bound_version is not None else candidate.version,
            operator_approved=operator_approved,
            cycle=cycle,
            failure_reason=failure_reason,
            artifact=dict(artifact or {}),
            evidence_refs=[plan.plan_id, candidate.candidate_id],
        )


def _protected_binding(
    inventory: CapabilityInventory, capability_id: str
) -> CapabilityBinding | None:
    existing = inventory.get(capability_id)
    if existing is None:
        return None
    if existing.state in _PROTECTED and inventory.usable(capability_id):
        return existing
    return None
