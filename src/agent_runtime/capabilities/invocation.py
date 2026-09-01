"""Read-only capability invocation boundary for the R2 integration slice."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .inventory import CapabilityInventory
from .model import CapabilityLifecycleState, CapabilityRequirement, SideEffectLevel


@dataclass(frozen=True)
class CapabilityInvocationResult:
    """Provider-neutral result for a bounded read-side invocation."""

    capability_id: str
    accepted: bool
    result: dict[str, Any] | None = None
    reason: str | None = None
    provider: str | None = None

    @property
    def blocked(self) -> bool:
        return not self.accepted

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "accepted": self.accepted,
            "blocked": self.blocked,
            "result": self.result,
            "reason": self.reason,
            "provider": self.provider,
        }


class ReadCapabilityGateway:
    """Invoke only local read-side capabilities through existing inventory."""

    def __init__(self, inventory: CapabilityInventory, provider: Any) -> None:
        self.inventory = inventory
        self.provider = provider

    def invoke(
        self,
        requirement: CapabilityRequirement,
        payload: Mapping[str, Any] | None = None,
    ) -> CapabilityInvocationResult:
        capability_id = requirement.capability_id
        binding = self.inventory.get(capability_id)
        if binding is None:
            return CapabilityInvocationResult(
                capability_id=capability_id,
                accepted=False,
                reason="capability_not_registered",
            )
        if binding.state not in {
            CapabilityLifecycleState.ACTIVE,
            CapabilityLifecycleState.DEGRADED,
        } or not self.inventory.usable(capability_id):
            return CapabilityInvocationResult(
                capability_id=capability_id,
                accepted=False,
                reason="capability_not_usable",
                provider=binding.provider,
            )
        if (
            requirement.max_side_effect.value > SideEffectLevel.READ_ONLY.value
            or binding.side_effects.value > SideEffectLevel.READ_ONLY.value
        ):
            return CapabilityInvocationResult(
                capability_id=capability_id,
                accepted=False,
                reason="read_gateway_side_effect_blocked",
                provider=binding.provider,
            )
        try:
            result = self.provider.invoke(capability_id, payload or {})
        except Exception as exc:  # provider failures remain bounded observations
            return CapabilityInvocationResult(
                capability_id=capability_id,
                accepted=False,
                reason=f"provider_error:{type(exc).__name__}",
                provider=binding.provider,
            )
        if not isinstance(result, Mapping):
            return CapabilityInvocationResult(
                capability_id=capability_id,
                accepted=False,
                reason="provider_result_not_mapping",
                provider=binding.provider,
            )
        return CapabilityInvocationResult(
            capability_id=capability_id,
            accepted=True,
            result=dict(result),
            provider=binding.provider,
        )
