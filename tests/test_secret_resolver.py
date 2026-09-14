from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_runtime.integrations.secrets import (
    ChainedSecretResolver,
    KeyringSecretResolver,
    MappingSecretResolver,
    SecretRef,
)


def test_secret_value_never_renders_plaintext() -> None:
    ref = SecretRef(provider="mapping", namespace="crawler", name="api-token")
    resolver = MappingSecretResolver({ref: "super-secret-value"})
    value = resolver.resolve(ref)

    assert value.reveal() == "super-secret-value"
    assert "super-secret-value" not in str(value)
    assert "super-secret-value" not in repr(value)
    assert "super-secret-value" not in repr(resolver)
    assert value.evidence() == ref.evidence()
    assert "super-secret-value" not in repr(value.evidence())


def test_secret_fingerprint_depends_on_reference_not_value() -> None:
    ref = SecretRef(provider="mapping", namespace="service", name="token")
    first = MappingSecretResolver({ref: "one"}).resolve(ref)
    second = MappingSecretResolver({ref: "two"}).resolve(ref)

    assert first.evidence()["fingerprint"] == second.evidence()["fingerprint"]
    assert first.reveal() != second.reveal()


def test_keyring_resolver_enforces_provider_and_service_allowlist() -> None:
    keyring = SimpleNamespace(
        get_password=lambda service, username: (
            "value" if (service, username) == ("runtime", "crawl4ai") else None
        )
    )
    resolver = KeyringSecretResolver(
        allowed_services=frozenset({"runtime"}), keyring_module=keyring
    )

    value = resolver.resolve(
        SecretRef(provider="keyring", namespace="runtime", name="crawl4ai")
    )
    assert value.reveal() == "value"

    with pytest.raises(PermissionError):
        resolver.resolve(
            SecretRef(provider="keyring", namespace="other", name="crawl4ai")
        )
    with pytest.raises(ValueError):
        resolver.resolve(
            SecretRef(provider="mapping", namespace="runtime", name="crawl4ai")
        )


def test_keyring_missing_secret_fails_without_name_or_value_leak_in_error() -> None:
    keyring = SimpleNamespace(get_password=lambda service, username: None)
    resolver = KeyringSecretResolver(
        allowed_services=frozenset({"runtime"}), keyring_module=keyring
    )
    ref = SecretRef(provider="keyring", namespace="runtime", name="private-token")

    with pytest.raises(LookupError) as caught:
        resolver.resolve(ref)
    assert "private-token" not in str(caught.value)
    assert ref.fingerprint in str(caught.value)


def test_chained_resolver_uses_later_provider_and_fails_closed() -> None:
    ref = SecretRef(provider="mapping", namespace="service", name="token")
    empty = MappingSecretResolver({"mapping:service:other": "x"})
    populated = MappingSecretResolver({ref: "value"})
    chain = ChainedSecretResolver(empty, populated)

    assert chain.resolve(ref).reveal() == "value"

    missing = SecretRef(provider="mapping", namespace="service", name="missing")
    with pytest.raises(LookupError, match="resolvers_tried=2"):
        chain.resolve(missing)
