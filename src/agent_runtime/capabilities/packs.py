"""Installed capability-pack discovery and governed ApplicationRuntime binding.

Capability packs are external Python distributions registered under the
``agent_runtime.capability_packs`` entry-point group. Runtime never installs a
package here: deployment decides what is installed and which pack IDs are
trusted. Agents only ask for provider-neutral capability IDs.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
import hashlib
from importlib import metadata
import json
import re
from typing import Any, Protocol, runtime_checkable

from ..application import ApplicationRuntime, CapabilityProvider
from .inventory import CapabilityInventory
from .model import CapabilityCandidate, CapabilityKind, SideEffectLevel
from .need import CapabilityNeed
from .policy import CapabilityPolicy
from .resolver import CapabilityResolver
from .sources.base import CapabilitySource

CAPABILITY_PACK_ENTRYPOINT_GROUP = "agent_runtime.capability_packs"
_PACK_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


class CapabilityPackError(ValueError):
    """An installed pack violates the Runtime pack contract."""


@runtime_checkable
class CapabilityPack(Protocol):
    """Cheap descriptor plus deferred provider construction."""

    pack_id: str
    version: str

    def manifest(self) -> Mapping[str, Any]: ...

    def build_provider(
        self, config: Mapping[str, Any] | None = None
    ) -> CapabilityProvider: ...


@dataclass(frozen=True)
class CapabilityPackDescriptor:
    pack_id: str
    version: str
    provider: str
    license: str | None
    capabilities: tuple[Mapping[str, Any], ...]

    def capability(self, capability_id: str) -> Mapping[str, Any] | None:
        return next(
            (row for row in self.capabilities if row["capability_id"] == capability_id),
            None,
        )


class CapabilityPackBindingStatus(str, Enum):
    BOUND = "BOUND"
    ALREADY_BOUND = "ALREADY_BOUND"
    MISSING = "MISSING"
    INCOMPATIBLE = "INCOMPATIBLE"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"


@dataclass(frozen=True)
class CapabilityPackBindingResult:
    capability_id: str
    status: CapabilityPackBindingStatus
    pack_id: str | None = None
    candidate_id: str | None = None
    provider: str | None = None
    search_id: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class ManifestCapabilityBindingReport:
    required: tuple[CapabilityPackBindingResult, ...]
    optional: tuple[CapabilityPackBindingResult, ...]

    @property
    def ready(self) -> bool:
        accepted = {
            CapabilityPackBindingStatus.BOUND,
            CapabilityPackBindingStatus.ALREADY_BOUND,
        }
        return all(item.status in accepted for item in self.required)

    @property
    def missing_required(self) -> tuple[str, ...]:
        accepted = {
            CapabilityPackBindingStatus.BOUND,
            CapabilityPackBindingStatus.ALREADY_BOUND,
        }
        return tuple(
            item.capability_id for item in self.required if item.status not in accepted
        )


class InstalledCapabilityPackCatalog:
    """Discover only deployment-trusted entry points; never install packages."""

    def __init__(
        self,
        *,
        trusted_pack_ids: Iterable[str],
        entry_points: Iterable[Any] | None = None,
    ) -> None:
        trusted = frozenset(str(item).strip() for item in trusted_pack_ids)
        if any(not _PACK_ID.fullmatch(item) for item in trusted):
            raise ValueError("trusted capability pack IDs must be normalized identifiers")
        self.trusted_pack_ids = trusted
        self._injected_entry_points = (
            tuple(entry_points) if entry_points is not None else None
        )
        self._packs: dict[str, CapabilityPack] = {}
        self._descriptors: dict[str, CapabilityPackDescriptor] = {}

    def pack_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    str(item.name)
                    for item in self._entry_points()
                    if str(item.name) in self.trusted_pack_ids
                }
            )
        )

    def descriptors(self) -> tuple[CapabilityPackDescriptor, ...]:
        return tuple(self.descriptor(pack_id) for pack_id in self.pack_ids())

    def descriptor(self, pack_id: str) -> CapabilityPackDescriptor:
        if pack_id not in self.trusted_pack_ids:
            raise CapabilityPackError(f"capability pack is not trusted: {pack_id}")
        cached = self._descriptors.get(pack_id)
        if cached is not None:
            return cached
        pack = self.pack(pack_id)
        descriptor = _descriptor(pack, expected_pack_id=pack_id)
        self._descriptors[pack_id] = descriptor
        return descriptor

    def pack(self, pack_id: str) -> CapabilityPack:
        if pack_id not in self.trusted_pack_ids:
            raise CapabilityPackError(f"capability pack is not trusted: {pack_id}")
        cached = self._packs.get(pack_id)
        if cached is not None:
            return cached
        matches = [item for item in self._entry_points() if str(item.name) == pack_id]
        if len(matches) != 1:
            raise CapabilityPackError(
                f"trusted capability pack requires exactly one installed entry point: {pack_id}"
            )
        loaded = matches[0].load()
        pack = loaded() if callable(loaded) else loaded
        if not isinstance(pack, CapabilityPack):
            raise CapabilityPackError(f"invalid capability pack object: {pack_id}")
        if pack.pack_id != pack_id:
            raise CapabilityPackError("entry-point name and capability pack ID differ")
        self._packs[pack_id] = pack
        return pack

    def _entry_points(self) -> tuple[Any, ...]:
        if self._injected_entry_points is not None:
            return tuple(
                item
                for item in self._injected_entry_points
                if getattr(item, "group", CAPABILITY_PACK_ENTRYPOINT_GROUP)
                == CAPABILITY_PACK_ENTRYPOINT_GROUP
            )
        discovered = metadata.entry_points()
        if hasattr(discovered, "select"):
            return tuple(discovered.select(group=CAPABILITY_PACK_ENTRYPOINT_GROUP))
        return tuple(discovered.get(CAPABILITY_PACK_ENTRYPOINT_GROUP, ()))


class InstalledCapabilityPackSource(CapabilitySource):
    """Expose trusted installed packs to the existing CapabilityResolver."""

    source_id = "installed-packs"

    def __init__(
        self,
        catalog: InstalledCapabilityPackCatalog,
        *,
        pack_ids: Iterable[str] | None = None,
    ) -> None:
        self.catalog = catalog
        self.pack_ids_filter = None if pack_ids is None else frozenset(pack_ids)

    def search(self, requirement) -> list[CapabilityCandidate]:
        out: list[CapabilityCandidate] = []
        for descriptor in self.catalog.descriptors():
            if self.pack_ids_filter is not None and descriptor.pack_id not in self.pack_ids_filter:
                continue
            row = descriptor.capability(requirement.capability_id)
            if row is None:
                continue
            out.append(
                CapabilityCandidate(
                    candidate_id=(
                        f"pack:{descriptor.pack_id}:{descriptor.version}:"
                        f"{requirement.capability_id}"
                    ),
                    capability_id=requirement.capability_id,
                    kind=_kind(row.get("kind", "SERVICE")),
                    provider=descriptor.provider,
                    description=str(row.get("description") or ""),
                    version=descriptor.version,
                    permissions=set(row.get("required_permissions") or ()),
                    side_effects=_side_effect(row.get("side_effect_level", "READ_ONLY")),
                    acquisition_mode="bind_installed",
                    source="installed-pack",
                    provenance={
                        "pack_id": descriptor.pack_id,
                        "pack_version": descriptor.version,
                        "maintained": row.get("maintained", True),
                        "task_fit": row.get("task_fit", 0.7),
                    },
                    license=descriptor.license,
                    evidence_refs=[
                        f"capability-pack:{descriptor.pack_id}:{descriptor.version}"
                    ],
                )
            )
        return out


class CapabilityPackManager:
    """Bind requested capabilities without granting an entire pack by accident."""

    def __init__(
        self,
        runtime: ApplicationRuntime,
        catalog: InstalledCapabilityPackCatalog,
        *,
        policy: CapabilityPolicy | None = None,
        resolver: CapabilityResolver | None = None,
    ) -> None:
        self.runtime = runtime
        self.catalog = catalog
        self.policy = policy or CapabilityPolicy()
        self.resolver = resolver or CapabilityResolver(policy=self.policy)
        self._providers: dict[str, CapabilityProvider] = {}
        self._config_fingerprints: dict[str, str] = {}

    def bind_need(
        self,
        need: CapabilityNeed,
        *,
        preferred_pack_id: str | None = None,
        config: Mapping[str, Any] | None = None,
        config_by_pack: Mapping[str, Mapping[str, Any]] | None = None,
        operator_approved: bool = False,
    ) -> CapabilityPackBindingResult:
        capability_id = need.capability_id
        if capability_id in self.runtime.available_capabilities():
            return CapabilityPackBindingResult(
                capability_id=capability_id,
                status=CapabilityPackBindingStatus.ALREADY_BOUND,
            )
        pack_ids = None if preferred_pack_id is None else (preferred_pack_id,)
        source = InstalledCapabilityPackSource(self.catalog, pack_ids=pack_ids)
        resolution = self.resolver.resolve(
            need,
            sources=[source],
            inventory=CapabilityInventory(),
            plan_step_id=None,
            cycle=self.runtime.cycle,
        )
        candidate_id = resolution.decision.candidate_id
        if candidate_id is None:
            rejected = [
                reason
                for assessment in resolution.assessments
                for reason in assessment.hard_fail_reasons
            ]
            status = (
                CapabilityPackBindingStatus.INCOMPATIBLE
                if resolution.search.candidate_ids
                else CapabilityPackBindingStatus.MISSING
            )
            return CapabilityPackBindingResult(
                capability_id=capability_id,
                status=status,
                search_id=resolution.search.search_id,
                reason=",".join(sorted(set(rejected))) or "no trusted installed pack",
            )
        candidates = source.search(need.requirement)
        candidate = next(item for item in candidates if item.candidate_id == candidate_id)
        pack_id = str(candidate.provenance["pack_id"])
        if self._approval_required(candidate) and not operator_approved:
            return CapabilityPackBindingResult(
                capability_id=capability_id,
                status=CapabilityPackBindingStatus.APPROVAL_REQUIRED,
                pack_id=pack_id,
                candidate_id=candidate_id,
                provider=candidate.provider,
                search_id=resolution.search.search_id,
                reason="binding exceeds automatic acquisition policy",
            )
        descriptor = self.catalog.descriptor(pack_id)
        row = descriptor.capability(capability_id)
        if row is None:
            raise CapabilityPackError("selected pack no longer declares capability")
        selected_config = config
        if selected_config is None and config_by_pack is not None:
            selected_config = config_by_pack.get(pack_id)
        provider = self._provider(descriptor, selected_config)
        _validate_provider_contract(provider, descriptor, row)
        selected = _SelectedCapabilityProvider(
            provider,
            descriptor=descriptor,
            capability_row=row,
        )
        self.runtime.register_provider(selected)
        return CapabilityPackBindingResult(
            capability_id=capability_id,
            status=CapabilityPackBindingStatus.BOUND,
            pack_id=pack_id,
            candidate_id=candidate_id,
            provider=descriptor.provider,
            search_id=resolution.search.search_id,
        )

    def reconcile_manifest(
        self,
        manifest: Any,
        *,
        preferred_pack_by_capability: Mapping[str, str] | None = None,
        config_by_pack: Mapping[str, Mapping[str, Any]] | None = None,
        operator_approved_capabilities: Iterable[str] = (),
    ) -> ManifestCapabilityBindingReport:
        preferences = dict(preferred_pack_by_capability or {})
        configs = dict(config_by_pack or {})
        approved = frozenset(operator_approved_capabilities)

        def bind(declaration: Any) -> CapabilityPackBindingResult:
            capability_id = declaration.capability_id
            max_side_effect = _side_effect(declaration.max_side_effect_level)
            need = CapabilityNeed.simple(
                capability_id,
                intent=declaration.intent,
                max_side_effect=max_side_effect,
            )
            need.requirement.required_permissions = set(declaration.required_permissions)
            preferred = preferences.get(capability_id)
            return self.bind_need(
                need,
                preferred_pack_id=preferred,
                config_by_pack=configs,
                operator_approved=capability_id in approved,
            )

        return ManifestCapabilityBindingReport(
            required=tuple(bind(item) for item in manifest.required_capabilities),
            optional=tuple(bind(item) for item in manifest.optional_capabilities),
        )

    def close(self) -> None:
        seen: set[int] = set()
        for provider in self._providers.values():
            if id(provider) in seen:
                continue
            seen.add(id(provider))
            close = getattr(provider, "close", None)
            if callable(close):
                close()

    def _approval_required(self, candidate: CapabilityCandidate) -> bool:
        if candidate.permissions:
            return True
        return not self.policy.may_auto_acquire(candidate.side_effects)

    def _provider(
        self,
        descriptor: CapabilityPackDescriptor,
        config: Mapping[str, Any] | None,
    ) -> CapabilityProvider:
        fingerprint = _config_fingerprint(config)
        cached = self._providers.get(descriptor.pack_id)
        if cached is not None:
            if self._config_fingerprints[descriptor.pack_id] != fingerprint:
                raise CapabilityPackError(
                    "capability pack config changed after provider construction"
                )
            return cached
        pack = self.catalog.pack(descriptor.pack_id)
        provider = pack.build_provider(dict(config or {}))
        if not isinstance(provider, CapabilityProvider):
            raise CapabilityPackError("capability pack returned an invalid provider")
        self._providers[descriptor.pack_id] = provider
        self._config_fingerprints[descriptor.pack_id] = fingerprint
        return provider


class _SelectedCapabilityProvider:
    """Least-authority wrapper: one request exposes one capability only."""

    def __init__(
        self,
        provider: CapabilityProvider,
        *,
        descriptor: CapabilityPackDescriptor,
        capability_row: Mapping[str, Any],
    ) -> None:
        self._provider = provider
        self._descriptor = descriptor
        self._row = dict(capability_row)
        self._capability_id = str(capability_row["capability_id"])
        self._provider_id = (
            f"{descriptor.provider}/{descriptor.pack_id}/{self._capability_id}"
        )

    def manifest(self) -> Mapping[str, Any]:
        row = dict(self._row)
        row["pack_id"] = self._descriptor.pack_id
        row["pack_version"] = self._descriptor.version
        row["upstream_provider"] = self._descriptor.provider
        return {
            "provider": self._provider_id,
            "version": self._descriptor.version,
            "capabilities": [row],
        }

    def invoke(
        self, capability_id: str, payload: Mapping[str, Any] | None = None
    ) -> Mapping[str, Any]:
        if capability_id != self._capability_id:
            raise ValueError("capability is outside the selected pack binding")
        return self._provider.invoke(capability_id, payload)


def _descriptor(pack: CapabilityPack, *, expected_pack_id: str) -> CapabilityPackDescriptor:
    if not _PACK_ID.fullmatch(str(pack.pack_id)) or pack.pack_id != expected_pack_id:
        raise CapabilityPackError("invalid capability pack ID")
    if not isinstance(pack.version, str) or not pack.version.strip():
        raise CapabilityPackError("capability pack version must be non-empty")
    raw = pack.manifest()
    if not isinstance(raw, Mapping):
        raise CapabilityPackError("capability pack manifest must be a mapping")
    if raw.get("pack_id") != pack.pack_id or raw.get("version") != pack.version:
        raise CapabilityPackError("capability pack object and manifest identity differ")
    provider = raw.get("provider")
    if not isinstance(provider, str) or not provider.strip():
        raise CapabilityPackError("capability pack manifest requires provider")
    rows = raw.get("capabilities")
    if not isinstance(rows, list) or not rows:
        raise CapabilityPackError("capability pack manifest requires capabilities")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in rows:
        if not isinstance(item, Mapping):
            raise CapabilityPackError("capability pack rows must be mappings")
        row = dict(item)
        capability_id = row.get("capability_id")
        if not isinstance(capability_id, str) or not capability_id.strip():
            raise CapabilityPackError("capability pack row requires capability_id")
        if capability_id in seen:
            raise CapabilityPackError("capability pack capability IDs must be unique")
        seen.add(capability_id)
        side_effect = _side_effect(row.get("side_effect_level", "READ_ONLY"))
        permissions = row.get("required_permissions") or []
        if not isinstance(permissions, list) or any(
            not isinstance(value, str) or not value for value in permissions
        ):
            raise CapabilityPackError("required_permissions must be a string array")
        _kind(row.get("kind", "SERVICE"))
        row["side_effect_level"] = side_effect.name
        row["required_permissions"] = list(permissions)
        normalized.append(row)
    license_id = raw.get("license")
    if license_id is not None and not isinstance(license_id, str):
        raise CapabilityPackError("capability pack license must be a string")
    return CapabilityPackDescriptor(
        pack_id=pack.pack_id,
        version=pack.version,
        provider=provider,
        license=license_id,
        capabilities=tuple(normalized),
    )


def _validate_provider_contract(
    provider: CapabilityProvider,
    descriptor: CapabilityPackDescriptor,
    expected_row: Mapping[str, Any],
) -> None:
    manifest = provider.manifest()
    if not isinstance(manifest, Mapping) or manifest.get("provider") != descriptor.provider:
        raise CapabilityPackError("provider identity does not match pack manifest")
    rows = manifest.get("capabilities")
    if not isinstance(rows, list):
        raise CapabilityPackError("provider manifest requires capabilities")
    capability_id = expected_row["capability_id"]
    matches = [row for row in rows if isinstance(row, Mapping) and row.get("capability_id") == capability_id]
    if len(matches) != 1:
        raise CapabilityPackError("provider does not expose selected capability exactly once")
    actual = matches[0]
    if _side_effect(actual.get("side_effect_level", "READ_ONLY")) != _side_effect(
        expected_row.get("side_effect_level", "READ_ONLY")
    ):
        raise CapabilityPackError("provider side-effect contract differs from pack manifest")
    if set(actual.get("required_permissions") or ()) != set(
        expected_row.get("required_permissions") or ()
    ):
        raise CapabilityPackError("provider permission contract differs from pack manifest")


def _side_effect(value: Any) -> SideEffectLevel:
    if isinstance(value, SideEffectLevel):
        return value
    if isinstance(value, str):
        try:
            return SideEffectLevel[value]
        except KeyError:
            pass
    raise CapabilityPackError(f"unknown side-effect level: {value!r}")


def _kind(value: Any) -> CapabilityKind:
    if isinstance(value, CapabilityKind):
        return value
    if isinstance(value, str):
        try:
            return CapabilityKind[value]
        except KeyError:
            try:
                return CapabilityKind(value)
            except ValueError:
                pass
    raise CapabilityPackError(f"unknown capability kind: {value!r}")


def _config_fingerprint(config: Mapping[str, Any] | None) -> str:
    try:
        canonical = json.dumps(
            dict(config or {}), sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise CapabilityPackError("capability pack config must be JSON-serializable") from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "CAPABILITY_PACK_ENTRYPOINT_GROUP",
    "CapabilityPack",
    "CapabilityPackBindingResult",
    "CapabilityPackBindingStatus",
    "CapabilityPackDescriptor",
    "CapabilityPackError",
    "CapabilityPackManager",
    "InstalledCapabilityPackCatalog",
    "InstalledCapabilityPackSource",
    "ManifestCapabilityBindingReport",
]
