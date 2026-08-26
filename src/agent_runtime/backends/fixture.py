"""Deterministic backend for unit tests and provider-free operation."""
from __future__ import annotations

from collections.abc import Callable

from .model import AgentBackendCapabilities, AgentRequest, AgentResult


class FixtureAgentBackend:
    def __init__(
        self,
        response: str | Callable[[AgentRequest], str] = "fixture response",
        *,
        backend_id: str = "fixture",
        capabilities: AgentBackendCapabilities | None = None,
    ) -> None:
        self._response = response
        self._backend_id = backend_id
        self._capabilities = capabilities or AgentBackendCapabilities(
            advisory_reasoning=True,
            structured_output=True,
        )
        self.requests: list[AgentRequest] = []

    @property
    def backend_id(self) -> str:
        return self._backend_id

    @property
    def capabilities(self) -> AgentBackendCapabilities:
        return self._capabilities

    async def run(self, request: AgentRequest) -> AgentResult:
        self.requests.append(request)
        text = self._response(request) if callable(self._response) else self._response
        return AgentResult(final_response=text)

