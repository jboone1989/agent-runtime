from __future__ import annotations

from agent_runtime.capabilities.inventory import CapabilityInventory
from agent_runtime.capabilities.model import (
    CapabilityBinding,
    CapabilityKind,
    CapabilityLifecycleState,
    CapabilityRequirement,
    SideEffectLevel,
)
from agent_runtime.capabilities.sources.local import LocalCapabilitySource
from agent_runtime.integrations.trading import register_trading_provider


class _Provider:
    def manifest(self):
        return {
            "provider": "trading",
            "version": "1.0",
            "capabilities": [
                {
                    "capability_id": "crypto.edge.discover",
                    "provider": "trading",
                    "version": "1.0",
                    "side_effect_level": "READ_ONLY",
                    "legacy_aliases": ["crypto.opportunity.discover_edges"],
                },
                {
                    "capability_id": "crypto.trading.execute",
                    "provider": "trading",
                    "version": "1.0",
                    "side_effect_level": "EXTERNAL_SIDE_EFFECT",
                    "legacy_aliases": ["crypto.arbitrage.execute"],
                },
            ],
        }

    def invoke(self, capability_id, payload):
        return {"ok": True, "capability_id": capability_id, "payload": payload}

    def health(self):
        return {"ok": True}


def test_trading_manifest_registers_local_active_bindings() -> None:
    inventory = CapabilityInventory()
    adapter = register_trading_provider(_Provider(), inventory)

    assert inventory.get("crypto.edge.discover").state is CapabilityLifecycleState.ACTIVE
    assert inventory.get("crypto.edge.discover").access_mode.value == "LOCAL"
    assert inventory.get("crypto.trading.execute").side_effects is SideEffectLevel.EXTERNAL_SIDE_EFFECT
    assert adapter.invoke("crypto.edge.discover", {"top": 2})["ok"] is True


def test_runtime_local_source_preserves_trading_side_effect_metadata() -> None:
    inventory = CapabilityInventory()
    register_trading_provider(_Provider(), inventory)

    candidate = LocalCapabilitySource(inventory).search(
        CapabilityRequirement(
            capability_id="crypto.trading.execute",
            max_side_effect=SideEffectLevel.EXTERNAL_SIDE_EFFECT,
        )
    )[0]

    assert candidate.side_effects is SideEffectLevel.EXTERNAL_SIDE_EFFECT


def test_legacy_binding_default_remains_none_for_existing_resolver_contract() -> None:
    inventory = CapabilityInventory()
    inventory.register(
        CapabilityBinding(
            capability_id="existing.local",
            kind=CapabilityKind.SERVICE,
            provider="existing",
        )
    )

    candidate = LocalCapabilitySource(inventory).search(
        CapabilityRequirement(capability_id="existing.local", max_side_effect=SideEffectLevel.NONE)
    )[0]

    assert candidate.side_effects is SideEffectLevel.NONE


def test_legacy_aliases_are_resolvable_and_keep_canonical_side_effects() -> None:
    inventory = CapabilityInventory()
    register_trading_provider(_Provider(), inventory)

    candidate = LocalCapabilitySource(inventory).search(
        CapabilityRequirement(
            capability_id="crypto.arbitrage.execute",
            max_side_effect=SideEffectLevel.EXTERNAL_SIDE_EFFECT,
        )
    )[0]

    assert candidate.side_effects is SideEffectLevel.EXTERNAL_SIDE_EFFECT
    assert inventory.get("crypto.arbitrage.execute").entry_point["capability_id"] == "crypto.trading.execute"
