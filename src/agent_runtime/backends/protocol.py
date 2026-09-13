"""Protocol implemented by model advisers and tool-using agent SDKs."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .model import AgentBackendCapabilities, AgentRequest, AgentResult


@runtime_checkable
class AgentBackend(Protocol):
    @property
    def backend_id(self) -> str: ...

    @property
    def capabilities(self) -> AgentBackendCapabilities: ...

    async def run(self, request: AgentRequest) -> AgentResult: ...

