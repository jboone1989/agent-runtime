"""Provider-neutral backend request, event, and result contracts."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any


class AgentRunMode(Enum):
    ADVISORY = "ADVISORY"
    EXECUTION = "EXECUTION"


@dataclass(frozen=True)
class AgentBackendCapabilities:
    advisory_reasoning: bool = False
    structured_output: bool = False
    workspace_execution: bool = False
    web_research: bool = False
    mcp: bool = False
    resumable_sessions: bool = False
    cancellable_execution: bool = False


@dataclass(frozen=True)
class ExecutionPolicy:
    allowed_tools: frozenset[str] = frozenset()
    allowed_resources: frozenset[str] = frozenset()
    allowed_networks: frozenset[str] = frozenset()
    workspace_write: bool = False
    require_approval: frozenset[str] = frozenset()

    @classmethod
    def advisory(cls) -> ExecutionPolicy:
        return cls()


@dataclass(frozen=True)
class AgentRequest:
    mode: AgentRunMode
    task: str
    context: dict[str, Any]
    policy: ExecutionPolicy = field(default_factory=ExecutionPolicy.advisory)
    output_schema: dict[str, Any] | None = None
    token_budget: int = 0
    compute_budget: float = 0.0
    previous_session_ref: str | None = None
    system: str | None = None
    max_duration_seconds: float | None = None

    def __post_init__(self) -> None:
        if not self.task.strip():
            raise ValueError("backend task must be non-empty")
        if self.token_budget < 0 or self.compute_budget < 0:
            raise ValueError("backend budgets must be non-negative")
        if self.max_duration_seconds is not None and (
            isinstance(self.max_duration_seconds, bool)
            or not isinstance(self.max_duration_seconds, (int, float))
            or not math.isfinite(self.max_duration_seconds)
            or self.max_duration_seconds <= 0
        ):
            raise ValueError("backend execution duration must be positive and finite")
        if self.mode is AgentRunMode.ADVISORY and self.policy != ExecutionPolicy.advisory():
            raise ValueError("ADVISORY requests cannot grant tools, resources, or network access")


@dataclass(frozen=True)
class AgentEvent:
    kind: str
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentArtifact:
    uri: str
    kind: str = "file"
    digest: str | None = None


@dataclass(frozen=True)
class AgentUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float | None = None
    currency: str | None = None


@dataclass(frozen=True)
class AgentResult:
    final_response: str
    status: str = "COMPLETED"
    events: tuple[AgentEvent, ...] = ()
    artifacts: tuple[AgentArtifact, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    session_ref: str | None = None
    usage: AgentUsage | None = None
    structured_output: dict[str, Any] | None = None
    error: str | None = None
