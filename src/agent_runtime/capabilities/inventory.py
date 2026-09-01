"""CapabilityInventory: objective 'what is actually available here'.

Inventory = reality (installed, connected, healthy).
SelfModel.capabilities = the agent's belief about reality.
Keep them separate: the inventory must never become a package manager,
and beliefs must never be auto-promoted by external claims.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..self_model.model import SelfModel
from .model import (
    CapabilityAccessMode,
    CapabilityBinding,
    CapabilityKind,
    CapabilityLifecycleState,
)

BUILTIN_CAPABILITY_IDS: tuple[str, ...] = (
    "observe",
    "introspect",
    "maintain",
    "diagnose",
    "llm_provider",
    # Action kinds emitted by planner/regulation in v0.2.x
    "noop",
    "record",
    "conserve",
    "switch_provider",
    "restore_config",
)
from .records import CapabilityHistoryRecord

BACKEND_CAPABILITY_IDS: tuple[str, ...] = (
    "advisory_reasoning",
    "structured_reasoning",
    "workspace_execution",
    "web_research",
    "mcp_access",
    "resumable_agent_session",
)

_EXECUTABLE_STATES = {
    CapabilityLifecycleState.ACTIVE,
    CapabilityLifecycleState.DEGRADED,
}
_LOCAL_MODES = {CapabilityAccessMode.OWNED, CapabilityAccessMode.LOCAL}


@dataclass
class CapabilityInventory:
    """Registry of bindings the runtime can actually use right now."""

    entries: dict[str, CapabilityBinding] = field(default_factory=dict)
    history: list[CapabilityHistoryRecord] = field(default_factory=list)

    # -- queries ---------------------------------------------------------

    def has(self, capability_id: str) -> bool:
        return capability_id in self.entries

    def get(self, capability_id: str) -> CapabilityBinding | None:
        return self.entries.get(capability_id)

    def usable(self, capability_id: str) -> bool:
        entry = self.entries.get(capability_id)
        if entry is None:
            return False
        if entry.access_mode in (CapabilityAccessMode.UNAVAILABLE, CapabilityAccessMode.ACQUIRABLE):
            return False
        return entry.state in _EXECUTABLE_STATES

    def usable_ids(self) -> list[str]:
        return sorted(cid for cid in self.entries if self.usable(cid))

    def local_ids(self) -> list[str]:
        return sorted(
            cid
            for cid, entry in self.entries.items()
            if entry.access_mode in _LOCAL_MODES and entry.state in _EXECUTABLE_STATES
        )

    def remote_access_ids(self) -> list[str]:
        return sorted(
            cid
            for cid, entry in self.entries.items()
            if entry.access_mode == CapabilityAccessMode.REMOTE_ACCESS
        )

    def health_of(self, capability_id: str) -> float:
        entry = self.entries.get(capability_id)
        return entry.health if entry is not None else 0.0

    # -- mutation --------------------------------------------------------

    def register(self, binding: CapabilityBinding) -> None:
        self.entries[binding.capability_id] = binding

    def set_state(self, capability_id: str, state: CapabilityLifecycleState) -> None:
        entry = self.entries.get(capability_id)
        if entry is not None:
            entry.state = state

    def set_health(self, capability_id: str, health: float) -> None:
        entry = self.entries.get(capability_id)
        if entry is not None:
            entry.health = max(0.0, min(1.0, float(health)))

    def retire(self, capability_id: str) -> None:
        self.set_state(capability_id, CapabilityLifecycleState.RETIRED)

    def seed_builtin(self) -> None:
        """Hardwired runtime capabilities. Always present unless disabled."""
        for cid in BUILTIN_CAPABILITY_IDS:
            if cid not in self.entries:
                self.entries[cid] = CapabilityBinding(
                    capability_id=cid,
                    kind=CapabilityKind.BUILTIN,
                    provider="agent-runtime",
                    access_mode=CapabilityAccessMode.OWNED,
                    state=CapabilityLifecycleState.ACTIVE,
                )

    def sync_backend_capabilities(
        self,
        available: dict[str, bool],
        *,
        provider: str,
    ) -> None:
        """Record actual backend features without inferring from vendor names."""

        for capability_id in BACKEND_CAPABILITY_IDS:
            enabled = bool(available.get(capability_id, False))
            self.entries[capability_id] = CapabilityBinding(
                capability_id=capability_id,
                kind=CapabilityKind.SERVICE,
                provider=provider,
                access_mode=(
                    CapabilityAccessMode.LOCAL
                    if enabled
                    else CapabilityAccessMode.UNAVAILABLE
                ),
                state=(
                    CapabilityLifecycleState.ACTIVE
                    if enabled
                    else CapabilityLifecycleState.DISCOVERED
                ),
                health=1.0 if enabled else 0.0,
                evidence_refs=[f"backend:{provider}"],
            )

    # -- SelfModel sync --------------------------------------------------

    def sync_to_self_model(self, self_model: SelfModel) -> None:
        """Project inventory reality into beliefs.

        Uses SelfModel.apply_update semantics: existing proficiency is
        preserved, external/remote access is never promoted to high
        proficiency. Availability reflects actual executability.
        """
        for cid, entry in self.entries.items():
            available = (
                entry.access_mode not in (CapabilityAccessMode.UNAVAILABLE, CapabilityAccessMode.ACQUIRABLE)
                and entry.state in _EXECUTABLE_STATES
            )
            uncertainty = 0.4 if entry.access_mode in _LOCAL_MODES else 0.6
            evidence = [f"inventory:{cid}", *entry.evidence_refs]
            self_model.apply_update(
                f"capabilities.{cid}",
                available,
                reason=f"inventory sync access={entry.access_mode.value} state={entry.state.value}",
                uncertainty=uncertainty,
                evidence=evidence,
            )
