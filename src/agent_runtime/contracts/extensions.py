"""Contract v1 extension registry. Does not change root schemas."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jsonschema

_FORBIDDEN_BARE = frozenset({"memory", "goals", "plans", "commitments"})
_SECRET_KEYS = frozenset({"secret", "api_key", "token", "password", "provider_handle"})


class ExtensionConflictError(ValueError):
    pass


class ExtensionValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ExtensionDescriptor:
    namespace: str
    version: str
    owner: str
    critical: bool
    schema_id: str


def _short_name(namespace: str) -> str:
    if namespace.startswith("extensions."):
        return namespace[len("extensions.") :]
    return namespace


def _scan_secrets(obj: Any, path: str = "") -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            lowered = str(key).lower()
            if lowered in _SECRET_KEYS or "secret" in lowered:
                raise ExtensionValidationError(
                    f"extension payload must not carry secrets: {path + key}"
                )
            _scan_secrets(value, path + str(key) + ".")
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _scan_secrets(item, f"{path}{i}.")


class ExtensionRegistry:
    def __init__(self) -> None:
        self._by_namespace: dict[str, tuple[ExtensionDescriptor, dict | None]] = {}
        self._by_short: dict[str, str] = {}

    def register(self, descriptor: ExtensionDescriptor, *, schema: dict | None = None) -> None:
        ns = descriptor.namespace
        short = _short_name(ns)
        head = short.split(".", 1)[0]
        if ns in _FORBIDDEN_BARE or short in _FORBIDDEN_BARE or head in _FORBIDDEN_BARE:
            raise ExtensionConflictError(f"namespace {ns!r} must not use a bare public name")
        if "." not in short and not ns.startswith("extensions."):
            raise ExtensionConflictError(f"namespace {ns!r} must not use a bare public name")
        if ns in self._by_namespace or short in self._by_short:
            raise ExtensionConflictError(f"namespace collision: {ns}")
        self._by_namespace[ns] = (descriptor, schema)
        self._by_short[short] = ns

    def filter_extensions(self, extensions: dict[str, Any] | None) -> dict[str, Any]:
        src = dict(extensions or {})
        out: dict[str, Any] = {}
        for key, value in src.items():
            if key in _FORBIDDEN_BARE:
                raise ExtensionConflictError(f"namespace {key!r} must not use a bare public name")
            ns = self._by_short.get(key) or (key if key in self._by_namespace else None)
            if ns is not None:
                _desc, schema = self._by_namespace[ns]
                if schema is not None:
                    try:
                        jsonschema.Draft202012Validator(schema).validate(value)
                    except jsonschema.ValidationError as exc:
                        raise ExtensionValidationError(str(exc)) from exc
                _scan_secrets(value)
                out[key] = value
                continue
            if isinstance(value, dict) and "critical" in value:
                if value.get("critical") is True:
                    raise ExtensionValidationError(
                        f"unknown critical extension {key!r} is rejected"
                    )
                continue
            _scan_secrets(value)
            out[key] = value
        return out
