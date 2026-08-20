from __future__ import annotations

from typing import Protocol

from ..sensing.signal import Signal
from .percept import Percept


class PerceptSignalMapper(Protocol):
    def accepts(self, percept: Percept) -> bool: ...

    def encode(self, percept: Percept) -> list[Signal]: ...


def _lineage(percept: Percept) -> list[str]:
    return list(percept.source_observation_ids) + [percept.percept_id]


class _KindMapper:
    kinds: frozenset[str] = frozenset()

    def accepts(self, percept: Percept) -> bool:
        return percept.kind in self.kinds

    def encode(self, percept: Percept) -> list[Signal]:
        from .encoder import encode_builtin

        return encode_builtin(percept, _lineage(percept))


class ResourceSignalMapper(_KindMapper):
    kinds = frozenset({"RESOURCE_LEVEL"})


class ProcessSignalMapper(_KindMapper):
    kinds = frozenset({"PROCESS_STATUS"})


class ProviderSignalMapper(_KindMapper):
    kinds = frozenset({"PROVIDER_STATUS"})


class IntegritySignalMapper(_KindMapper):
    kinds = frozenset(
        {
            "CONFIG_STATE",
            "CREDENTIAL_STATE",
            "SIGNATURE_STATE",
            "DEPENDENCY_HASH_STATE",
            "AUTH_STATE",
        }
    )


class MemorySignalMapper(_KindMapper):
    kinds = frozenset({"MEMORY_STATUS"})


class PhysicalResourceMapper(_KindMapper):
    kinds = frozenset({"PROXIMITY", "THERMAL_LEVEL"})


class BuiltinSignalMapper(_KindMapper):
    """Union of built-in kinds. New environments should register extra mappers instead."""

    kinds = (
        ResourceSignalMapper.kinds
        | ProcessSignalMapper.kinds
        | ProviderSignalMapper.kinds
        | IntegritySignalMapper.kinds
        | MemorySignalMapper.kinds
        | PhysicalResourceMapper.kinds
    )


def default_mappers() -> list[PerceptSignalMapper]:
    return [
        ResourceSignalMapper(),
        ProcessSignalMapper(),
        ProviderSignalMapper(),
        IntegritySignalMapper(),
        MemorySignalMapper(),
        PhysicalResourceMapper(),
    ]
