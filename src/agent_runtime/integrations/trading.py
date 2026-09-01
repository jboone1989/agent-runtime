"""Adapter from Trading's stable capability facade to Runtime inventory.

Trading remains a domain provider. This module only translates its ordinary
JSON manifest into the Runtime's existing CapabilityBinding model and keeps a
reference to the provider for explicit invocation by a later execution
boundary. It does not create a second registry or execution path.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..capabilities.inventory import CapabilityInventory
from ..capabilities.model import (
    CapabilityAccessMode,
    CapabilityBinding,
    CapabilityKind,
    CapabilityLifecycleState,
    SideEffectLevel,
)


class TradingCapabilityAdapter:
    """Register and invoke one Trading provider through Runtime contracts."""

    def __init__(self, provider: Any) -> None:
        self.provider = provider
        self._manifest: dict[str, Any] | None = None

    def manifest(self) -> dict[str, Any]:
        if self._manifest is None:
            raw = self.provider.manifest()
            if not isinstance(raw, Mapping):
                raise TypeError("Trading provider manifest must be a mapping")
            self._manifest = dict(raw)
        return dict(self._manifest)

    def register(self, inventory: CapabilityInventory) -> tuple[str, ...]:
        manifest = self.manifest()
        provider_name = _required_string(manifest, "provider")
        version = _required_string(manifest, "version")
        rows = manifest.get("capabilities")
        if not isinstance(rows, list):
            raise ValueError("Trading provider manifest capabilities must be a list")

        prepared: list[tuple[str, str, SideEffectLevel, tuple[str, ...]]] = []
        seen_ids: set[str] = set()
        for row in rows:
            if not isinstance(row, Mapping):
                raise TypeError("Trading capability manifest rows must be mappings")
            capability_id = _required_string(row, "capability_id")
            if row.get("provider") != provider_name:
                raise ValueError(f"capability provider mismatch: {capability_id}")
            row_version = _required_string(row, "version")
            side_effects = _side_effect_level(row.get("side_effect_level"))
            aliases = _aliases(row.get("legacy_aliases"), canonical_id=capability_id)
            for registered_id in (capability_id, *aliases):
                if registered_id in seen_ids:
                    raise ValueError(f"Trading capability ID collision: {registered_id}")
                if inventory.get(registered_id) is not None:
                    raise ValueError(f"Trading capability already registered: {registered_id}")
                seen_ids.add(registered_id)
            prepared.append((capability_id, row_version, side_effects, aliases))

        registered: list[str] = []
        for capability_id, row_version, side_effects, aliases in prepared:
            for registered_id in (capability_id, *aliases):
                inventory.register(
                    CapabilityBinding(
                        capability_id=registered_id,
                        kind=CapabilityKind.SERVICE,
                        provider=provider_name,
                        candidate_id=f"{provider_name}:{capability_id}",
                        access_mode=CapabilityAccessMode.LOCAL,
                        state=CapabilityLifecycleState.ACTIVE,
                        version=row_version or version,
                        side_effects=side_effects,
                        entry_point={
                            "adapter": "TradingCapabilityAdapter",
                            "capability_id": capability_id,
                            "provider": provider_name,
                            "legacy_aliases": list(aliases),
                        },
                        evidence_refs=["manifest:trading"],
                    )
                )
                registered.append(registered_id)
        return tuple(registered)

    def invoke(self, capability_id: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        result = self.provider.invoke(capability_id, payload or {})
        if not isinstance(result, Mapping):
            raise TypeError("Trading provider invocation must return a mapping")
        return dict(result)

    def health(self) -> dict[str, Any]:
        result = self.provider.health()
        if not isinstance(result, Mapping):
            raise TypeError("Trading provider health must return a mapping")
        return dict(result)


def register_trading_provider(
    provider: Any, inventory: CapabilityInventory
) -> TradingCapabilityAdapter:
    adapter = TradingCapabilityAdapter(provider)
    adapter.register(inventory)
    return adapter


def _required_string(row: Mapping[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Trading capability manifest requires non-empty {field}")
    return value


def _side_effect_level(value: Any) -> SideEffectLevel:
    if not isinstance(value, str):
        raise ValueError("Trading capability manifest requires side_effect_level")
    try:
        return SideEffectLevel[value]
    except KeyError as exc:
        raise ValueError(f"unknown Trading side effect level: {value}") from exc


def _aliases(value: Any, *, canonical_id: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError("Trading capability legacy_aliases must be a list of non-empty strings")
    if len(set(value)) != len(value):
        raise ValueError("Trading capability legacy_aliases must be unique")
    if canonical_id in value:
        raise ValueError(f"Trading capability alias duplicates canonical ID: {canonical_id}")
    return tuple(value)
