from __future__ import annotations

from agent_runtime.capabilities import (
    CapabilityAccessMode,
    CapabilityBinding,
    CapabilityKind,
    CapabilityInventory,
    CapabilityLifecycleState,
    CapabilityRequirement,
    ReadCapabilityGateway,
    ShadowCapabilityGateway,
    SideEffectLevel,
)
from agent_runtime.integrations import register_trading_provider


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
                    "legacy_aliases": [],
                },
                {
                    "capability_id": "crypto.trading.execute",
                    "provider": "trading",
                    "version": "1.0",
                    "side_effect_level": "EXTERNAL_SIDE_EFFECT",
                    "legacy_aliases": [],
                },
            ],
        }

    def __init__(self):
        self.calls = []

    def invoke(self, capability_id, payload):
        self.calls.append((capability_id, payload))
        return {"ok": True, "capability_id": capability_id}


def test_read_gateway_invokes_local_trading_capability() -> None:
    provider = _Provider()
    inventory = CapabilityInventory()
    register_trading_provider(provider, inventory)
    gateway = ReadCapabilityGateway(inventory, provider)

    result = gateway.invoke(
        CapabilityRequirement(
            capability_id="crypto.edge.discover",
            max_side_effect=SideEffectLevel.READ_ONLY,
        ),
        {"top": 2},
    )

    assert result.accepted is True
    assert result.result == {"ok": True, "capability_id": "crypto.edge.discover"}
    assert provider.calls == [("crypto.edge.discover", {"top": 2})]


def test_read_gateway_fails_closed_for_external_side_effect() -> None:
    provider = _Provider()
    inventory = CapabilityInventory()
    register_trading_provider(provider, inventory)
    gateway = ReadCapabilityGateway(inventory, provider)

    result = gateway.invoke(
        CapabilityRequirement(
            capability_id="crypto.trading.execute",
            max_side_effect=SideEffectLevel.EXTERNAL_SIDE_EFFECT,
        ),
        {"opportunity_id": "opp-1", "capital": 10},
    )

    assert result.to_dict()["reason"] == "read_gateway_side_effect_blocked"
    assert provider.calls == []


def test_shadow_gateway_allows_local_state_but_not_external_effects() -> None:
    class _ShadowProvider(_Provider):
        def manifest(self):
            manifest = super().manifest()
            manifest["capabilities"][0]["side_effect_level"] = "LOCAL_STATE"
            return manifest

    provider = _ShadowProvider()
    inventory = CapabilityInventory()
    register_trading_provider(provider, inventory)
    gateway = ShadowCapabilityGateway(inventory, provider)

    local_state = gateway.invoke(
        CapabilityRequirement(
            capability_id="crypto.edge.discover",
            max_side_effect=SideEffectLevel.LOCAL_STATE,
        )
    )
    external = gateway.invoke(
        CapabilityRequirement(
            capability_id="crypto.trading.execute",
            max_side_effect=SideEffectLevel.LOCAL_STATE,
        )
    )

    assert local_state.accepted is True
    assert external.reason == "read_gateway_side_effect_blocked"
    assert len(provider.calls) == 1


def test_read_gateway_converts_provider_failure_to_bounded_result() -> None:
    class _FailingProvider(_Provider):
        def invoke(self, capability_id, payload):
            raise RuntimeError("unavailable")

    provider = _FailingProvider()
    inventory = CapabilityInventory()
    register_trading_provider(provider, inventory)

    result = ReadCapabilityGateway(inventory, provider).invoke(
        CapabilityRequirement(capability_id="crypto.edge.discover")
    )

    assert result.accepted is False
    assert result.reason == "provider_error:RuntimeError"


def test_read_gateway_rejects_degraded_binding_before_provider_call() -> None:
    provider = _Provider()
    inventory = CapabilityInventory()
    register_trading_provider(provider, inventory)
    inventory.set_state("crypto.edge.discover", CapabilityLifecycleState.DEGRADED)

    result = ReadCapabilityGateway(inventory, provider).invoke(
        CapabilityRequirement(capability_id="crypto.edge.discover")
    )

    assert result.reason == "capability_not_usable"
    assert provider.calls == []


def test_read_gateway_rejects_remote_or_mismatched_bindings() -> None:
    provider = _Provider()
    inventory = CapabilityInventory(
        entries={
            "remote.read": CapabilityBinding(
                capability_id="remote.read",
                kind=CapabilityKind.REMOTE_AGENT,
                provider="trading",
                access_mode=CapabilityAccessMode.REMOTE_ACCESS,
                side_effects=SideEffectLevel.READ_ONLY,
            ),
            "other.read": CapabilityBinding(
                capability_id="other.read",
                kind=CapabilityKind.SERVICE,
                provider="other-provider",
                access_mode=CapabilityAccessMode.LOCAL,
                side_effects=SideEffectLevel.READ_ONLY,
            ),
        }
    )
    gateway = ReadCapabilityGateway(inventory, provider)

    remote = gateway.invoke(CapabilityRequirement(capability_id="remote.read"))
    other = gateway.invoke(CapabilityRequirement(capability_id="other.read"))

    assert remote.reason == "capability_provider_mismatch"
    assert other.reason == "capability_provider_mismatch"
    assert provider.calls == []
