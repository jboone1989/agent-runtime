"""Compatibility adapter for the old text-only provider during migration."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from .model import (
    AgentBackendCapabilities,
    AgentRequest,
    AgentResult,
    AgentRunMode,
)


class LegacyProviderBackend:
    """Expose a legacy ``complete`` provider as an advisory-only backend."""

    def __init__(self, provider: Any, *, backend_id: str = "legacy-provider") -> None:
        if not callable(getattr(provider, "complete", None)):
            raise TypeError("legacy provider must implement async complete(prompt, **kwargs)")
        self.provider = provider
        self._backend_id = backend_id

    @property
    def backend_id(self) -> str:
        return self._backend_id

    @property
    def capabilities(self) -> AgentBackendCapabilities:
        return AgentBackendCapabilities(
            advisory_reasoning=True,
            structured_output=callable(getattr(self.provider, "structured", None)),
        )

    async def run(self, request: AgentRequest) -> AgentResult:
        if request.mode is not AgentRunMode.ADVISORY:
            raise PermissionError("legacy providers are advisory-only")
        # Preserve the old provider prompt shape during migration. New backends
        # receive the versioned envelope; legacy tests and providers see only
        # the projected sections they historically consumed.
        context = request.context
        if context.get("schema_version") == "1.0" and isinstance(
            context.get("sections"), dict
        ):
            context = context["sections"]
        prompt = (
            "Runtime state:\n"
            f"{json.dumps(context, ensure_ascii=False)}\n\n"
            f"Task:\n{request.task}"
        )
        kwargs: dict[str, Any] = {}
        if request.token_budget:
            kwargs["max_tokens"] = request.token_budget
        if request.system:
            kwargs["system"] = request.system
        kwargs["_routing_task_class"] = request.routing_task_class
        if request.minimum_model_quality is not None:
            kwargs["_minimum_quality"] = float(request.minimum_model_quality)
        if request.max_duration_seconds is not None:
            total_budget = float(request.max_duration_seconds)
            # Long-lived agent cognition often has a generous outer budget (for
            # example a ChatGPT watcher plus provider fallbacks). Do not force
            # every free fallback through the historical 15s ceiling: several
            # empirically viable models have >15s mean latency on real prompts.
            attempt_ceiling = 30.0 if total_budget >= 120.0 else 15.0
            attempt_timeout = max(
                1.0, min(attempt_ceiling, total_budget / 4.0)
            )
            reserve_timeout = max(
                attempt_timeout,
                total_budget - attempt_timeout - 5.0,
            )
            kwargs["_attempt_timeout_seconds"] = attempt_timeout
            kwargs["_reserve_timeout_seconds"] = reserve_timeout
        structured = getattr(self.provider, "structured", None)
        if request.output_schema is not None and callable(structured):
            call = structured(prompt, request.output_schema, **kwargs)
            value = await _bounded(call, request.max_duration_seconds)
            if not isinstance(value, dict):
                raise ValueError("legacy provider structured response must be a JSON object")
            return AgentResult(
                final_response=json.dumps(value, ensure_ascii=False),
                structured_output=value,
            )
        call = self.provider.complete(prompt, **kwargs)
        response = await _bounded(call, request.max_duration_seconds)
        return AgentResult(final_response=str(response))


async def _bounded(call: Any, timeout_seconds: float | None) -> Any:
    if timeout_seconds is None:
        return await call
    try:
        return await asyncio.wait_for(call, timeout=float(timeout_seconds))
    except asyncio.TimeoutError as exc:
        raise TimeoutError("legacy advisory backend exceeded max_duration_seconds") from exc
