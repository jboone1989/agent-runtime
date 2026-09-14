from __future__ import annotations

from contextlib import contextmanager

import pytest

from agent_runtime.application import ApplicationRuntime
from agent_runtime.telemetry import OpenTelemetryHooks


class _Span:
    def __init__(self, attributes):
        self.attributes = dict(attributes)
        self.exceptions = []
        self.statuses = []

    def set_attribute(self, key, value):
        self.attributes[key] = value

    def record_exception(self, exc):
        self.exceptions.append(repr(exc))

    def set_status(self, status):
        self.statuses.append(status)


class _Tracer:
    def __init__(self):
        self.spans = []
        self.options = []

    @contextmanager
    def start_as_current_span(self, name, attributes=None, **kwargs):
        span = _Span(attributes or {})
        span.name = name
        self.spans.append(span)
        self.options.append(dict(kwargs))
        yield span


class _Provider:
    def manifest(self):
        return {
            "provider": "fake",
            "capabilities": [
                {
                    "capability_id": "demo.read",
                    "side_effect_level": "READ_ONLY",
                    "required_permissions": [],
                },
                {
                    "capability_id": "demo.write",
                    "side_effect_level": "LOCAL_STATE",
                    "required_permissions": ["demo.write"],
                },
                {
                    "capability_id": "demo.fail",
                    "side_effect_level": "READ_ONLY",
                    "required_permissions": [],
                },
            ],
        }

    def invoke(self, capability_id, payload=None):
        if capability_id == "demo.fail":
            raise RuntimeError("provider-secret-value")
        return {"ok": True, "echo": dict(payload or {})}


def test_application_runtime_emits_metadata_only_span(tmp_path) -> None:
    tracer = _Tracer()
    runtime = ApplicationRuntime.bootstrap(
        tmp_path,
        subject_id="subject",
        telemetry=OpenTelemetryHooks(tracer=tracer),
    )
    runtime.register_provider(_Provider())

    receipt = runtime.invoke("demo.read", {"secret": "do-not-export"})

    assert receipt.payload["echo"]["secret"] == "do-not-export"
    span = tracer.spans[0]
    assert span.name == "agent_runtime.capability.invoke"
    assert span.attributes == {
        "agent_runtime.capability.id": "demo.read",
        "agent_runtime.provider": "fake",
        "agent_runtime.side_effect_level": "READ_ONLY",
        "agent_runtime.success": True,
    }
    assert tracer.options[0] == {
        "record_exception": False,
        "set_status_on_exception": False,
    }
    assert "do-not-export" not in repr(span.attributes)


def test_application_runtime_records_authorization_failure_without_payload(tmp_path) -> None:
    tracer = _Tracer()
    runtime = ApplicationRuntime.bootstrap(
        tmp_path,
        subject_id="subject",
        telemetry=OpenTelemetryHooks(tracer=tracer),
    )
    runtime.register_provider(_Provider())

    with pytest.raises(PermissionError, match="missing capability permissions"):
        runtime.invoke("demo.write", {"secret": "still-private"})

    span = tracer.spans[0]
    assert span.attributes["agent_runtime.success"] is False
    assert span.attributes["agent_runtime.error.type"] == "PermissionError"
    assert span.exceptions == []
    assert "still-private" not in repr(span.attributes)


def test_provider_exception_message_is_not_exported(tmp_path) -> None:
    tracer = _Tracer()
    runtime = ApplicationRuntime.bootstrap(
        tmp_path,
        subject_id="subject",
        telemetry=OpenTelemetryHooks(tracer=tracer),
    )
    runtime.register_provider(_Provider())

    with pytest.raises(RuntimeError, match="provider-secret-value"):
        runtime.invoke("demo.fail", {"secret": "request-secret-value"})

    span = tracer.spans[0]
    exported = repr((span.attributes, span.exceptions))
    assert span.attributes["agent_runtime.error.type"] == "RuntimeError"
    assert span.exceptions == []
    assert "provider-secret-value" not in exported
    assert "request-secret-value" not in exported


def test_telemetry_survives_runtime_restart_without_persistence(tmp_path) -> None:
    first_tracer = _Tracer()
    first = ApplicationRuntime.bootstrap(
        tmp_path,
        subject_id="subject",
        telemetry=OpenTelemetryHooks(tracer=first_tracer),
    )
    first.register_provider(_Provider())
    first.invoke("demo.read", {})
    first.persist()

    second_tracer = _Tracer()
    second = ApplicationRuntime.bootstrap(
        tmp_path,
        subject_id="subject",
        telemetry=OpenTelemetryHooks(tracer=second_tracer),
    )
    second.register_provider(_Provider())
    second.invoke("demo.read", {})

    assert len(first_tracer.spans) == 1
    assert len(second_tracer.spans) == 1
