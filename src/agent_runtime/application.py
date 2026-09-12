"""Stable embeddable application kernel.

The application kernel has no subject identity of its own.  It references the
embedding application's persistent subject id and owns capability routing,
authorization ceilings, invocation receipts, and framework-cycle state.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable


APPLICATION_API_VERSION = "1.0"
_STATE_FILE = "application_runtime.json"
_SIDE_EFFECT_ORDER = {
    "NONE": 0,
    "READ_ONLY": 1,
    "LOCAL_STATE": 2,
    "EXTERNAL_SIDE_EFFECT": 3,
    "IRREVERSIBLE": 4,
}


@runtime_checkable
class CapabilityProvider(Protocol):
    """Application-supplied capability pack."""

    def manifest(self) -> Mapping[str, Any]: ...

    def invoke(
        self, capability_id: str, payload: Mapping[str, Any] | None = None
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class CapabilityInvocationResult:
    receipt_id: str
    cycle: int
    capability_id: str
    provider: str
    payload: Mapping[str, Any]


class ApplicationRuntime:
    """Embeddable framework kernel for one persistent application subject."""

    api_version = APPLICATION_API_VERSION

    def __init__(
        self,
        *,
        state_dir: Path,
        subject_id: str,
        cycle: int = 0,
        max_side_effect_level: str = "LOCAL_STATE",
        allowed_permissions: frozenset[str] = frozenset(),
        receipts: tuple[Mapping[str, Any], ...] = (),
    ) -> None:
        if not subject_id.strip():
            raise ValueError("subject_id must be non-empty")
        if max_side_effect_level not in _SIDE_EFFECT_ORDER:
            raise ValueError("unknown max_side_effect_level")
        self._state_dir = state_dir
        self._subject_id = subject_id
        self._cycle = int(cycle)
        self._max_side_effect_level = max_side_effect_level
        self._allowed_permissions = frozenset(allowed_permissions)
        self._providers: dict[str, CapabilityProvider] = {}
        self._capability_provider: dict[str, str] = {}
        self._capability_manifest: dict[str, dict[str, Any]] = {}
        self._receipts: list[dict[str, Any]] = [dict(item) for item in receipts][-200:]

    @classmethod
    def bootstrap(
        cls,
        state_dir: str | Path,
        *,
        subject_id: str,
        max_side_effect_level: str = "LOCAL_STATE",
        allowed_permissions: frozenset[str] = frozenset(),
    ) -> "ApplicationRuntime":
        root = Path(state_dir)
        path = root / _STATE_FILE
        if not path.exists():
            return cls(
                state_dir=root,
                subject_id=subject_id,
                max_side_effect_level=max_side_effect_level,
                allowed_permissions=allowed_permissions,
            )
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("api_version") != APPLICATION_API_VERSION:
            raise ValueError("application runtime state version mismatch")
        persisted_subject = str(raw.get("subject_id") or "")
        if persisted_subject != subject_id:
            raise ValueError(
                f"subject_id mismatch: requested={subject_id!r} persisted={persisted_subject!r}"
            )
        return cls(
            state_dir=root,
            subject_id=subject_id,
            cycle=int(raw.get("cycle") or 0),
            max_side_effect_level=max_side_effect_level,
            allowed_permissions=allowed_permissions,
            receipts=tuple(raw.get("receipts") or ()),
        )

    @property
    def subject_id(self) -> str:
        return self._subject_id

    @property
    def cycle(self) -> int:
        return self._cycle

    @property
    def receipts(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(deepcopy(item) for item in self._receipts)

    def next_cycle(self) -> int:
        self._cycle += 1
        return self._cycle

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
            side_effect = str(row.get("side_effect_level") or "READ_ONLY")
            if side_effect not in _SIDE_EFFECT_ORDER:
                raise ValueError(f"unknown side_effect_level: {side_effect}")
            permissions = row.get("required_permissions") or []
            if not isinstance(permissions, list) or any(
                not isinstance(item, str) or not item for item in permissions
            ):
                raise ValueError("required_permissions must be a string array")
            normalized = deepcopy(dict(row))
            normalized["side_effect_level"] = side_effect
            normalized["required_permissions"] = list(permissions)
            rows[capability_id] = normalized

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
        row = self._capability_manifest[capability_id]
        side_effect = str(row["side_effect_level"])
        if _SIDE_EFFECT_ORDER[side_effect] > _SIDE_EFFECT_ORDER[self._max_side_effect_level]:
            raise PermissionError(
                f"capability side effect {side_effect} exceeds runtime ceiling {self._max_side_effect_level}"
            )
        required = frozenset(row.get("required_permissions") or ())
        missing = sorted(required - self._allowed_permissions)
        if missing:
            raise PermissionError(f"missing capability permissions: {','.join(missing)}")

        args = dict(payload or {})
        result = self._providers[provider_id].invoke(capability_id, args)
        if not isinstance(result, Mapping):
            raise TypeError("capability provider result must be a mapping")
        result_payload = dict(result)
        receipt_body = {
            "subject_id": self._subject_id,
            "cycle": self._cycle,
            "capability_id": capability_id,
            "provider": provider_id,
            "request": args,
            "result": result_payload,
        }
        canonical = json.dumps(
            receipt_body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        receipt_id = "capability-receipt:v1:" + hashlib.sha256(canonical.encode()).hexdigest()
        self._receipts.append({"receipt_id": receipt_id, **receipt_body})
        self._receipts = self._receipts[-200:]
        return CapabilityInvocationResult(
            receipt_id=receipt_id,
            cycle=self._cycle,
            capability_id=capability_id,
            provider=provider_id,
            payload=result_payload,
        )

    def persist(self) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        path = self._state_dir / _STATE_FILE
        tmp = path.with_suffix(path.suffix + ".tmp")
        payload = {
            "api_version": APPLICATION_API_VERSION,
            "subject_id": self._subject_id,
            "cycle": self._cycle,
            "receipts": self._receipts,
        }
        tmp.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)


__all__ = [
    "APPLICATION_API_VERSION",
    "ApplicationRuntime",
    "CapabilityInvocationResult",
    "CapabilityProvider",
]
