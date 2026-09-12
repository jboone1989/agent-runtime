"""Stable embeddable application API.

Applications depend on this surface instead of Agent Runtime internals.  The
standalone CLI remains a framework test/demo surface, not an application
composition requirement.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

from agent_runtime.motivation.engine import MotivationEngine
from agent_runtime.observations import Observation
from agent_runtime.runtime import AgentRuntime, CycleResult


APPLICATION_API_VERSION = "1.0"


@runtime_checkable
class CapabilityProvider(Protocol):
    """Application-supplied capability pack."""

    def manifest(self) -> Mapping[str, Any]: ...

    def invoke(
        self, capability_id: str, payload: Mapping[str, Any] | None = None
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class CapabilityInvocationResult:
    capability_id: str
    provider: str
    payload: Mapping[str, Any]


class ApplicationRuntime:
    """Embeddable Agent Runtime facade for one application process."""

    api_version = APPLICATION_API_VERSION

    def __init__(self, runtime: AgentRuntime) -> None:
        self._runtime = runtime
        self._providers: dict[str, CapabilityProvider] = {}
        self._capability_provider: dict[str, str] = {}
        self._capability_manifest: dict[str, dict[str, Any]] = {}

    @classmethod
    def bootstrap(
        cls,
        state_dir: str | Path,
        *,
        motivation_engine: MotivationEngine | None = None,
        **runtime_kwargs: Any,
    ) -> "ApplicationRuntime":
        root = Path(state_dir)
        if (root / "agent_state.json").exists():
            runtime = AgentRuntime.restore(
                root, motivation_engine=motivation_engine, **runtime_kwargs
            )
        else:
            runtime = AgentRuntime.bootstrap(
                root, motivation_engine=motivation_engine, **runtime_kwargs
            )
        return cls(runtime)

    @property
    def agent_id(self) -> str:
        return self._runtime.identity.agent_id

    @property
    def cycle(self) -> int:
        return self._runtime.cycle

    def register_provider(self, provider: CapabilityProvider) -> None:
        if not isinstance(provider, CapabilityProvider):
            raise TypeError("provider must implement CapabilityProvider")
        manifest = dict(provider.manifest())
        provider_id = manifest.get("provider")
        capabilities = manifest.get("capabilities")
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise ValueError("provider manifest requires a non-empty provider")
        if provider_id in self._providers:
            raise ValueError(f"provider already registered: {provider_id}")
        if not isinstance(capabilities, list) or not capabilities:
            raise ValueError("provider manifest requires non-empty capabilities")

        rows: dict[str, dict[str, Any]] = {}
        for row in capabilities:
            if not isinstance(row, Mapping):
                raise ValueError("capability manifest rows must be mappings")
            capability_id = row.get("capability_id")
            if not isinstance(capability_id, str) or not capability_id.strip():
                raise ValueError("capability manifest row requires capability_id")
            if capability_id in rows or capability_id in self._capability_provider:
                raise ValueError(f"duplicate capability_id: {capability_id}")
            rows[capability_id] = deepcopy(dict(row))

        self._providers[provider_id] = provider
        for capability_id, row in rows.items():
            self._capability_provider[capability_id] = provider_id
            self._capability_manifest[capability_id] = row

    def available_capabilities(self) -> tuple[str, ...]:
        return tuple(sorted(self._capability_provider))

    def capability_manifest(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(
            deepcopy(self._capability_manifest[capability_id])
            for capability_id in sorted(self._capability_manifest)
        )

    def invoke(
        self, capability_id: str, payload: Mapping[str, Any] | None = None
    ) -> CapabilityInvocationResult:
        provider_id = self._capability_provider.get(capability_id)
        if provider_id is None:
            raise LookupError(f"capability not registered: {capability_id}")
        result = self._providers[provider_id].invoke(capability_id, payload)
        if not isinstance(result, Mapping):
            raise TypeError("capability provider result must be a mapping")
        return CapabilityInvocationResult(
            capability_id=capability_id,
            provider=provider_id,
            payload=dict(result),
        )

    def step(self, observation: Observation | None = None) -> CycleResult:
        return self._runtime.step(observation)

    def persist(self) -> None:
        self._runtime.persist()


__all__ = [
    "APPLICATION_API_VERSION",
    "ApplicationRuntime",
    "CapabilityInvocationResult",
    "CapabilityProvider",
    "Observation",
]
