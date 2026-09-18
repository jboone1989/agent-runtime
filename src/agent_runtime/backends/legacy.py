"""Compatibility adapter for the old text-only provider during migration."""
from __future__ import annotations

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
        return AgentBackendCapabilities(advisory_reasoning=True)

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
        response = await self.provider.complete(prompt, **kwargs)
        return AgentResult(final_response=str(response))
