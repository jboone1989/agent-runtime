from __future__ import annotations

import json

import pytest

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


def test_registration_prevalidates_collisions_without_partial_inventory_mutation() -> None:
    class _CollidingProvider(_Provider):
        def manifest(self):
            manifest = super().manifest()
            manifest["capabilities"].append(
                {
                    "capability_id": "crypto.other",
                    "provider": "trading",
                    "version": "1.0",
                    "side_effect_level": "READ_ONLY",
                    "legacy_aliases": ["crypto.arbitrage.execute"],
                }
            )
            return manifest

    inventory = CapabilityInventory()
    with pytest.raises(ValueError, match="collision"):
        register_trading_provider(_CollidingProvider(), inventory)

    assert inventory.entries == {}


def test_alias_metadata_is_json_round_trippable() -> None:
    inventory = CapabilityInventory()
    register_trading_provider(_Provider(), inventory)

    binding = inventory.get("crypto.arbitrage.execute")
    restored = json.loads(json.dumps({"binding": {
        "entry_point": binding.entry_point,
        "side_effects": binding.side_effects.name,
    }}))["binding"]

    assert restored["entry_point"]["legacy_aliases"] == ["crypto.arbitrage.execute"]
    assert restored["side_effects"] == "EXTERNAL_SIDE_EFFECT"
