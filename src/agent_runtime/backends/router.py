"""Capability-based backend selection without vendor checks in domain code."""
from __future__ import annotations

from .model import AgentRunMode
from .protocol import AgentBackend


class AgentBackendRouter:
    def __init__(self, backends: list[AgentBackend] | None = None) -> None:
        self._backends: dict[str, AgentBackend] = {}
        for backend in backends or []:
            self.register(backend)

    def register(self, backend: AgentBackend) -> None:
        if backend.backend_id in self._backends:
            raise ValueError(f"duplicate backend_id: {backend.backend_id}")
        self._backends[backend.backend_id] = backend

    def get(self, backend_id: str) -> AgentBackend:
        try:
            return self._backends[backend_id]
        except KeyError as exc:
            raise LookupError(f"unknown backend: {backend_id}") from exc

    def select(self, mode: AgentRunMode) -> AgentBackend:
        for backend in self._backends.values():
            capabilities = backend.capabilities
            if mode is AgentRunMode.ADVISORY and capabilities.advisory_reasoning:
                return backend
            if mode is AgentRunMode.EXECUTION and capabilities.workspace_execution:
                return backend
        raise LookupError(f"no backend supports {mode.value}")

