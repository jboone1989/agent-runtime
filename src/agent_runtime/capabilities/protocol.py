"""Language-neutral capability invocation envelope.

ApplicationRuntime already owns routing, permission ceilings and durable
receipts.  This module defines the payload contract used *inside* one
capability invocation so an implementation can move between Python, Rust, Go,
Java, MCP or another process without changing agent code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable
from uuid import uuid4

CAPABILITY_PROTOCOL_VERSION = "1.0"


class CapabilityProtocolError(ValueError):
    """A provider or caller violated the stable capability envelope."""


@dataclass(frozen=True)
class CapabilityCall:
    request_id: str
    subject_id: str
    capability_id: str
    operation: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        subject_id: str,
        capability_id: str,
        operation: str,
        payload: Mapping[str, Any] | None = None,
        request_id: str | None = None,
    ) -> "CapabilityCall":
        return cls(
            request_id=request_id or f"capcall_{uuid4().hex}",
            subject_id=_non_empty(subject_id, "subject_id"),
            capability_id=_non_empty(capability_id, "capability_id"),
            operation=_non_empty(operation, "operation"),
            payload=dict(payload or {}),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "capability_protocol_version": CAPABILITY_PROTOCOL_VERSION,
            "request_id": self.request_id,
            "subject_id": self.subject_id,
            "capability_id": self.capability_id,
            "operation": self.operation,
            "payload": dict(self.payload),
        }

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        expected_capability_id: str | None = None,
    ) -> "CapabilityCall":
        raw = dict(value)
        _expect_keys(
            raw,
            {
                "capability_protocol_version",
                "request_id",
                "subject_id",
                "capability_id",
                "operation",
                "payload",
            },
            "capability call",
        )
        if raw["capability_protocol_version"] != CAPABILITY_PROTOCOL_VERSION:
            raise CapabilityProtocolError("unsupported capability protocol version")
        capability_id = _non_empty(raw["capability_id"], "capability_id")
        if expected_capability_id is not None and capability_id != expected_capability_id:
            raise CapabilityProtocolError("capability id does not match provider binding")
        payload = raw["payload"]
        if not isinstance(payload, Mapping):
            raise CapabilityProtocolError("payload must be a mapping")
        return cls(
            request_id=_non_empty(raw["request_id"], "request_id"),
            subject_id=_non_empty(raw["subject_id"], "subject_id"),
            capability_id=capability_id,
            operation=_non_empty(raw["operation"], "operation"),
            payload=dict(payload),
        )


@dataclass(frozen=True)
class CapabilityReply:
    request_id: str
    capability_id: str
    provider: str
    ok: bool
    payload: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    evidence_refs: tuple[str, ...] = ()

    @classmethod
    def success(
        cls,
        call: CapabilityCall,
        *,
        provider: str,
        payload: Mapping[str, Any] | None = None,
        evidence_refs: tuple[str, ...] | list[str] = (),
    ) -> "CapabilityReply":
        return cls(
            request_id=call.request_id,
            capability_id=call.capability_id,
            provider=_non_empty(provider, "provider"),
            ok=True,
            payload=dict(payload or {}),
            evidence_refs=tuple(str(item) for item in evidence_refs),
        )

    @classmethod
    def failure(
        cls,
        call: CapabilityCall,
        *,
        provider: str,
        error_code: str,
        error_message: str,
        evidence_refs: tuple[str, ...] | list[str] = (),
    ) -> "CapabilityReply":
        return cls(
            request_id=call.request_id,
            capability_id=call.capability_id,
            provider=_non_empty(provider, "provider"),
            ok=False,
            error_code=_non_empty(error_code, "error_code"),
            error_message=_non_empty(error_message, "error_message"),
            evidence_refs=tuple(str(item) for item in evidence_refs),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "capability_protocol_version": CAPABILITY_PROTOCOL_VERSION,
            "request_id": self.request_id,
            "capability_id": self.capability_id,
            "provider": self.provider,
            "ok": self.ok,
            "payload": dict(self.payload),
            "error_code": self.error_code,
            "error_message": self.error_message,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        expected_call: CapabilityCall | None = None,
    ) -> "CapabilityReply":
        raw = dict(value)
        _expect_keys(
            raw,
            {
                "capability_protocol_version",
                "request_id",
                "capability_id",
                "provider",
                "ok",
                "payload",
                "error_code",
                "error_message",
                "evidence_refs",
            },
            "capability reply",
        )
        if raw["capability_protocol_version"] != CAPABILITY_PROTOCOL_VERSION:
            raise CapabilityProtocolError("unsupported capability protocol version")
        if type(raw["ok"]) is not bool:
            raise CapabilityProtocolError("ok must be a boolean")
        payload = raw["payload"]
        if not isinstance(payload, Mapping):
            raise CapabilityProtocolError("payload must be a mapping")
        refs = raw["evidence_refs"]
        if not isinstance(refs, list) or any(not isinstance(item, str) for item in refs):
            raise CapabilityProtocolError("evidence_refs must be a string array")
        reply = cls(
            request_id=_non_empty(raw["request_id"], "request_id"),
            capability_id=_non_empty(raw["capability_id"], "capability_id"),
            provider=_non_empty(raw["provider"], "provider"),
            ok=raw["ok"],
            payload=dict(payload),
            error_code=_optional_string(raw["error_code"], "error_code"),
            error_message=_optional_string(raw["error_message"], "error_message"),
            evidence_refs=tuple(refs),
        )
        if reply.ok and (reply.error_code is not None or reply.error_message is not None):
            raise CapabilityProtocolError("successful reply cannot contain an error")
        if not reply.ok and (reply.error_code is None or reply.error_message is None):
            raise CapabilityProtocolError("failed reply requires error_code and error_message")
        if expected_call is not None:
            if reply.request_id != expected_call.request_id:
                raise CapabilityProtocolError("reply request id does not match call")
            if reply.capability_id != expected_call.capability_id:
                raise CapabilityProtocolError("reply capability id does not match call")
        return reply


@runtime_checkable
class CapabilityInvoker(Protocol):
    @property
    def subject_id(self) -> str: ...

    def invoke(
        self, capability_id: str, payload: Mapping[str, Any] | None = None
    ) -> Any: ...


class CapabilityClient:
    """Typed caller facade over ApplicationRuntime or another compatible invoker."""

    def __init__(self, invoker: CapabilityInvoker, capability_id: str) -> None:
        if not isinstance(invoker, CapabilityInvoker):
            raise TypeError("invoker must expose subject_id and invoke()")
        self.invoker = invoker
        self.capability_id = _non_empty(capability_id, "capability_id")

    def call(
        self,
        operation: str,
        payload: Mapping[str, Any] | None = None,
        *,
        request_id: str | None = None,
    ) -> CapabilityReply:
        call = CapabilityCall.create(
            subject_id=self.invoker.subject_id,
            capability_id=self.capability_id,
            operation=operation,
            payload=payload,
            request_id=request_id,
        )
        runtime_result = self.invoker.invoke(self.capability_id, call.to_mapping())
        raw = getattr(runtime_result, "payload", runtime_result)
        if not isinstance(raw, Mapping):
            raise CapabilityProtocolError("runtime returned a non-mapping capability reply")
        reply = CapabilityReply.from_mapping(raw, expected_call=call)
        if not reply.ok:
            raise CapabilityProtocolError(
                f"{reply.error_code}: {reply.error_message}"
            )
        return reply


def _non_empty(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CapabilityProtocolError(f"{field_name} must be a non-empty string")
    return value


def _optional_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise CapabilityProtocolError(f"{field_name} must be a non-empty string or null")
    return value


def _expect_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        extra = sorted(actual - expected)
        missing = sorted(expected - actual)
        raise CapabilityProtocolError(
            f"{label} fields mismatch; missing={missing} extra={extra}"
        )


__all__ = [
    "CAPABILITY_PROTOCOL_VERSION",
    "CapabilityCall",
    "CapabilityClient",
    "CapabilityInvoker",
    "CapabilityProtocolError",
    "CapabilityReply",
]
