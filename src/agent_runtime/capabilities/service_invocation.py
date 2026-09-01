"""Verified, durable service invocation orchestration for the R2/R3 path."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..clock import new_id
from ..codec import dump, load
from ..goals.goal import Goal
from ..planning.step import PlanStep
from .invocation import (
    CapabilityInvocationResult,
    ReadCapabilityGateway,
    ShadowCapabilityGateway,
)
from .inventory import CapabilityInventory
from .model import CapabilityDecisionAction
from .need import CapabilityNeed
from .resolver import CapabilityResolver, CapabilityResolution
from .sources.base import CapabilitySource
from .sources.local import LocalCapabilitySource


@dataclass(frozen=True)
class ServiceVerification:
    status: str
    verified: bool
    summary: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ServiceInvocationEvidence:
    invocation_id: str
    timestamp: datetime
    cycle: int
    goal_id: str
    plan_step_id: str
    capability_id: str
    provider: str | None
    resolution_action: str | None
    invocation: dict[str, Any]
    verification: ServiceVerification
    success: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


VerificationFn = Callable[
    [Goal, PlanStep, CapabilityInvocationResult], ServiceVerification
]


class VerifiedShadowService:
    """Route a plan step through resolver → shadow gateway → verification."""

    def __init__(
        self,
        *,
        inventory: CapabilityInventory,
        provider: Any,
        ledger_path: Path,
        resolver: CapabilityResolver | None = None,
        sources: list[CapabilitySource] | None = None,
        verifier: VerificationFn | None = None,
    ) -> None:
        self.inventory = inventory
        self.provider = provider
        self.ledger_path = Path(ledger_path)
        self.resolver = resolver or CapabilityResolver()
        self.sources = sources or [LocalCapabilitySource(inventory)]
        self.verifier = verifier or _default_verifier
        self._records: list[ServiceInvocationEvidence] = []
        if self.ledger_path.exists():
            restored = load(self.ledger_path)
            if not isinstance(restored, list) or not all(
                isinstance(item, ServiceInvocationEvidence) for item in restored
            ):
                raise ValueError("invalid verified service invocation ledger")
            self._records = restored

    @property
    def records(self) -> tuple[ServiceInvocationEvidence, ...]:
        return tuple(self._records)

    def invoke(
        self,
        *,
        goal: Goal,
        plan_step: PlanStep,
        cycle: int,
        shadow: bool = False,
    ) -> ServiceInvocationEvidence:
        requirement = _single_requirement(plan_step)
        resolution: CapabilityResolution | None = None
        provider_result = CapabilityInvocationResult(
            capability_id=requirement.capability_id,
            accepted=False,
            reason="capability_not_resolved",
        )
        try:
            need = CapabilityNeed(
                requirement=requirement,
                evidence_refs=[*goal.evidence_refs, *plan_step.evidence_refs],
            )
            resolution = self.resolver.resolve(
                need,
                sources=self.sources,
                inventory=self.inventory,
                plan_step_id=plan_step.step_id,
                cycle=cycle,
            )
            if resolution.decision.action is CapabilityDecisionAction.REUSE_LOCAL:
                gateway = (
                    ShadowCapabilityGateway(self.inventory, self.provider)
                    if shadow
                    else ReadCapabilityGateway(self.inventory, self.provider)
                )
                provider_result = gateway.invoke(requirement, plan_step.payload)
            else:
                provider_result = CapabilityInvocationResult(
                    capability_id=requirement.capability_id,
                    accepted=False,
                    reason="capability_not_reuse_local",
                )
        except Exception as exc:
            provider_result = CapabilityInvocationResult(
                capability_id=requirement.capability_id,
                accepted=False,
                reason=f"orchestration_error:{type(exc).__name__}",
            )

        try:
            verification = self.verifier(goal, plan_step, provider_result)
        except Exception as exc:
            verification = ServiceVerification(
                status="FAIL",
                verified=False,
                summary="verification failed with exception",
                evidence={"error": f"{type(exc).__name__}: {exc}"},
            )
        record = ServiceInvocationEvidence(
            invocation_id=new_id("svc"),
            timestamp=datetime.now(timezone.utc),
            cycle=int(cycle),
            goal_id=goal.goal_id,
            plan_step_id=plan_step.step_id,
            capability_id=requirement.capability_id,
            provider=provider_result.provider,
            resolution_action=(
                None if resolution is None else resolution.decision.action.name
            ),
            invocation=provider_result.to_dict(),
            verification=verification,
            success=provider_result.accepted and verification.verified,
        )
        self._records.append(record)
        dump(self._records, self.ledger_path)
        return record


def _single_requirement(plan_step: PlanStep):
    if len(plan_step.required_capabilities) != 1:
        raise ValueError("R4 requires exactly one capability requirement per step")
    return plan_step.required_capabilities[0]


def _default_verifier(
    _goal: Goal,
    _plan_step: PlanStep,
    invocation: CapabilityInvocationResult,
) -> ServiceVerification:
    if not invocation.accepted:
        return ServiceVerification(
            status="FAIL",
            verified=False,
            summary="capability invocation was not accepted",
            evidence={"reason": invocation.reason},
        )
    if not isinstance(invocation.result, dict) or invocation.result.get("ok") is not True:
        return ServiceVerification(
            status="FAIL",
            verified=False,
            summary="provider result did not satisfy the observation contract",
        )
    return ServiceVerification(
        status="PASS",
        verified=True,
        summary="provider result satisfied the observation contract",
    )
