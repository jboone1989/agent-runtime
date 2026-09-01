"""Governed use, degradation, replacement, retirement, and removal."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .inventory import CapabilityInventory
from .model import CapabilityBinding, CapabilityLifecycleState
from .records import CapabilityHistoryRecord, CapabilityUsageRecord


@dataclass(frozen=True)
class RetirementDecision:
    capability_id: str
    reason: str
    cycle: int
    replacement_reference: str | None = None
    last_use_cycle: int | None = None
    health: float = 0.0
    failure_count: int = 0
    dependency_check: str = "passed"
    evidence_refs: tuple[str, ...] = ()


class CapabilityLifecycleService:
    """The bounded lifecycle policy for currently installed bindings.

    This service stores no provider-specific behavior. It only changes the
    inventory after Runtime-owned usage evidence and dependency checks exist.
    """

    def __init__(
        self,
        inventory: CapabilityInventory,
        usage: list[CapabilityUsageRecord],
        history: list[CapabilityHistoryRecord] | None = None,
        *,
        health_window: int = 8,
        degrade_after_failures: int = 3,
        degrade_below_health: float = 0.5,
    ) -> None:
        if health_window < 1 or degrade_after_failures < 1:
            raise ValueError("lifecycle bounds must be positive")
        self.inventory = inventory
        self.usage = usage
        self.history = history if history is not None else inventory.history
        self.health_window = health_window
        self.degrade_after_failures = degrade_after_failures
        self.degrade_below_health = float(degrade_below_health)

    def record_use(self, record: CapabilityUsageRecord) -> None:
        """Persist one outcome and update binding health from recent evidence."""
        self.usage.append(record)
        binding = self.inventory.get(record.capability_id)
        if binding is None:
            return
        recent = [
            item
            for item in self.usage
            if item.capability_id == record.capability_id
        ][-self.health_window :]
        if not recent:
            return
        binding.health = max(
            0.0,
            min(1.0, 0.8 * binding.health + 0.2 * (1.0 if record.success else 0.0)),
        )
        failures = sum(not item.success for item in recent)
        if (
            failures >= self.degrade_after_failures
            or binding.health < self.degrade_below_health
        ) and binding.state == CapabilityLifecycleState.ACTIVE:
            binding.state = CapabilityLifecycleState.DEGRADED

    def replace(
        self,
        capability_id: str,
        replacement: CapabilityBinding,
        *,
        cycle: int,
        reason: str,
        evidence_refs: Iterable[str] = (),
    ) -> CapabilityHistoryRecord:
        current = self.inventory.get(capability_id)
        if current is None:
            raise KeyError(capability_id)
        if replacement.capability_id != capability_id:
            raise ValueError("replacement must preserve capability identity")
        if replacement.state != CapabilityLifecycleState.ACTIVE:
            raise ValueError("replacement binding must be ACTIVE")
        if current.state not in {
            CapabilityLifecycleState.ACTIVE,
            CapabilityLifecycleState.DEGRADED,
        }:
            raise ValueError("only an executable binding can be replaced")
        old_usage = [item for item in self.usage if item.capability_id == capability_id]
        history = self._history(current, cycle=cycle, reason=reason, usage=old_usage)
        history.replacement_reference = replacement.binding_id or replacement.candidate_id
        history.evidence_refs = list(dict.fromkeys(evidence_refs))
        self.history.append(history)
        self.inventory.register(replacement)
        return history

    def retire(
        self,
        capability_id: str,
        *,
        cycle: int,
        reason: str,
        replacement_reference: str | None = None,
        evidence_refs: Iterable[str] = (),
    ) -> RetirementDecision:
        binding = self.inventory.get(capability_id)
        if binding is None:
            raise KeyError(capability_id)
        binding.state = CapabilityLifecycleState.RETIRED
        recent = [item for item in self.usage if item.capability_id == capability_id]
        failures = sum(not item.success for item in recent)
        last_cycle = recent[-1].cycle if recent else None
        self.history.append(
            self._history(binding, cycle=cycle, reason=reason, usage=recent)
        )
        return RetirementDecision(
            capability_id=capability_id,
            reason=reason,
            cycle=cycle,
            replacement_reference=replacement_reference,
            last_use_cycle=last_cycle,
            health=binding.health,
            failure_count=failures,
            evidence_refs=tuple(dict.fromkeys(evidence_refs)),
        )

    def remove(
        self,
        capability_id: str,
        *,
        active_invocations: Iterable[str] = (),
        unresolved_receipts: Iterable[str] = (),
        plan_dependencies: Iterable[str] = (),
        rollback_dependencies: Iterable[str] = (),
    ) -> None:
        binding = self.inventory.get(capability_id)
        if binding is None:
            raise KeyError(capability_id)
        blockers = {
            "active invocation": list(active_invocations),
            "unresolved receipt": list(unresolved_receipts),
            "plan dependency": list(plan_dependencies),
            "rollback dependency": list(rollback_dependencies),
        }
        if any(blockers.values()):
            raise ValueError(
                "cannot remove capability with dependencies: "
                + "; ".join(name for name, refs in blockers.items() if refs)
            )
        if binding.state != CapabilityLifecycleState.RETIRED:
            raise ValueError("capability must be RETIRED before removal")
        del self.inventory.entries[capability_id]

    def _history(
        self,
        binding: CapabilityBinding,
        *,
        cycle: int,
        reason: str,
        usage: list[CapabilityUsageRecord],
    ) -> CapabilityHistoryRecord:
        return CapabilityHistoryRecord(
            capability_id=binding.capability_id,
            binding_id=binding.binding_id,
            candidate_id=binding.candidate_id,
            provider=binding.provider,
            retired_at_cycle=cycle,
            why_retired=reason,
            verified_usage_count=sum(item.evidence_result == "COVERED" for item in usage),
            successful_usage_count=sum(item.success for item in usage),
            evidence_refs=[
                ref
                for item in usage
                for ref in item.evidence_refs
            ],
        )
