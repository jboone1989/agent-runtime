"""Optional platform telemetry hooks; never model-facing and never payload-bearing."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RuntimeTelemetry(Protocol):
    def capability_invocation(
        self,
        *,
        capability_id: str,
        provider: str,
        side_effect_level: str,
    ) -> AbstractContextManager[None]: ...


class NoOpRuntimeTelemetry:
    @contextmanager
    def capability_invocation(
        self,
        *,
        capability_id: str,
        provider: str,
        side_effect_level: str,
    ) -> Iterator[None]:
        yield


class OpenTelemetryHooks:
    """Create bounded invocation spans without request/result/secret attributes."""

    def __init__(self, tracer: Any | None = None) -> None:
        if tracer is None:
            try:
                from opentelemetry import trace
            except ImportError as exc:
                raise RuntimeError(
                    "telemetry extra missing; install agent-runtime[telemetry]"
                ) from exc
            tracer = trace.get_tracer("agent_runtime", "0.3.0")
        if not callable(getattr(tracer, "start_as_current_span", None)):
            raise TypeError("telemetry tracer must support start_as_current_span")
        self._tracer = tracer

    @contextmanager
    def capability_invocation(
        self,
        *,
        capability_id: str,
        provider: str,
        side_effect_level: str,
    ) -> Iterator[None]:
        attributes = {
            "agent_runtime.capability.id": str(capability_id)[:256],
            "agent_runtime.provider": str(provider)[:256],
            "agent_runtime.side_effect_level": str(side_effect_level)[:64],
        }
        with self._tracer.start_as_current_span(
            "agent_runtime.capability.invoke", attributes=attributes
        ) as span:
            try:
                yield
            except Exception as exc:
                setter = getattr(span, "set_attribute", None)
                if callable(setter):
                    setter("agent_runtime.success", False)
                    setter("agent_runtime.error.type", type(exc).__name__[:256])
                recorder = getattr(span, "record_exception", None)
                if callable(recorder):
                    recorder(exc)
                _set_error_status(span)
                raise
            else:
                setter = getattr(span, "set_attribute", None)
                if callable(setter):
                    setter("agent_runtime.success", True)


def _set_error_status(span: Any) -> None:
    setter = getattr(span, "set_status", None)
    if not callable(setter):
        return
    try:
        from opentelemetry.trace import Status, StatusCode
    except ImportError:
        return
    setter(Status(StatusCode.ERROR))


__all__ = ["NoOpRuntimeTelemetry", "OpenTelemetryHooks", "RuntimeTelemetry"]
