from __future__ import annotations

from agent_runtime.capabilities import (
    CapabilityInventory,
    CapabilityRequirement,
    ReadCapabilityGateway,
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
