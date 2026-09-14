"""Internal credential resolution without model-facing secret capabilities."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Any, Mapping, Protocol, runtime_checkable


@dataclass(frozen=True)
class SecretRef:
    """Opaque reference to a secret. It contains no credential value."""

    provider: str
    name: str
    namespace: str = ""

    def __post_init__(self) -> None:
        for field_name in ("provider", "name"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"secret {field_name} must be non-empty")
        if not isinstance(self.namespace, str):
            raise ValueError("secret namespace must be a string")

    @property
    def canonical(self) -> str:
        return f"{self.provider.strip()}:{self.namespace.strip()}:{self.name.strip()}"

    @property
    def fingerprint(self) -> str:
        digest = hashlib.sha256(self.canonical.encode("utf-8")).hexdigest()
        return f"secret-ref:v1:{digest}"

    def evidence(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "namespace": self.namespace,
            "name": self.name,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True, repr=False)
class SecretValue:
    """Redacting wrapper. Call ``reveal()`` only at the provider boundary."""

    ref: SecretRef
    _value: str = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self._value, str) or not self._value:
            raise ValueError("resolved secret value must be a non-empty string")

    def reveal(self) -> str:
        return self._value

    def evidence(self) -> dict[str, str]:
        return self.ref.evidence()

    def __str__(self) -> str:
        return "<secret:redacted>"

    def __repr__(self) -> str:
        return f"SecretValue(ref={self.ref!r}, value=<redacted>)"


@runtime_checkable
class SecretResolver(Protocol):
    def resolve(self, ref: SecretRef) -> SecretValue: ...


class MappingSecretResolver:
    """Explicit in-memory resolver for tests or process-local bootstrap wiring."""

    def __init__(
        self,
        values: Mapping[str | SecretRef, str],
        *,
        provider: str = "mapping",
    ) -> None:
        self.provider = provider
        normalized: dict[str, str] = {}
        for key, value in values.items():
            canonical = key.canonical if isinstance(key, SecretRef) else str(key)
            if not canonical or not isinstance(value, str) or not value:
                raise ValueError("mapping secrets require non-empty keys and values")
            normalized[canonical] = value
        self._values = normalized

    def resolve(self, ref: SecretRef) -> SecretValue:
        value = self._values.get(ref.canonical)
        if value is None:
            raise LookupError(f"secret reference not found: {ref.fingerprint}")
        return SecretValue(ref=ref, _value=value)

    def __repr__(self) -> str:
        return (
            f"MappingSecretResolver(provider={self.provider!r}, "
            f"entries={len(self._values)})"
        )


class KeyringSecretResolver:
    """Resolve approved refs from the host OS keyring.

    ``namespace`` maps to the keyring service name and ``name`` maps to the
    username. The optional service allowlist prevents arbitrary keyring reads.
    """

    def __init__(
        self,
        *,
        allowed_services: frozenset[str],
        keyring_module: Any | None = None,
    ) -> None:
        if not allowed_services or any(
            not isinstance(item, str) or not item.strip()
            for item in allowed_services
        ):
            raise ValueError("keyring allowed_services must be non-empty")
        self.allowed_services = frozenset(item.strip() for item in allowed_services)
        self._keyring_module = keyring_module

    def resolve(self, ref: SecretRef) -> SecretValue:
        if ref.provider != "keyring":
            raise ValueError("KeyringSecretResolver accepts only keyring refs")
        service = ref.namespace.strip()
        if not service or service not in self.allowed_services:
            raise PermissionError("secret keyring service is not allowed")
        keyring = self._keyring_module or _load_keyring()
        value = keyring.get_password(service, ref.name)
        if not isinstance(value, str) or not value:
            raise LookupError(f"secret reference not found: {ref.fingerprint}")
        return SecretValue(ref=ref, _value=value)


class ChainedSecretResolver:
    """Try explicit resolvers in order without weakening any resolver policy."""

    def __init__(self, *resolvers: SecretResolver) -> None:
        if not resolvers:
            raise ValueError("at least one secret resolver is required")
        self._resolvers = tuple(resolvers)

    def resolve(self, ref: SecretRef) -> SecretValue:
        failures: list[Exception] = []
        for resolver in self._resolvers:
            try:
                return resolver.resolve(ref)
            except (LookupError, ValueError, PermissionError) as exc:
                failures.append(exc)
        raise LookupError(
            f"secret reference unresolved: {ref.fingerprint}; "
            f"resolvers_tried={len(failures)}"
        )


def _load_keyring() -> Any:
    try:
        import keyring
    except ImportError as exc:
        raise RuntimeError(
            "secrets extra missing; install agent-runtime[secrets]"
        ) from exc
    return keyring


__all__ = [
    "ChainedSecretResolver",
    "KeyringSecretResolver",
    "MappingSecretResolver",
    "SecretRef",
    "SecretResolver",
    "SecretValue",
]
