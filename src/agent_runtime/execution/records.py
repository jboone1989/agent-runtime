"""Records produced by the Runtime execution gateway."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..backends.model import AgentResult
from ..records import ActionRecord, OutcomeRecord
from .verification import VerificationResult


@dataclass(frozen=True)
class RuntimeExecutionResult:
    """Orchestration result. Authority remains on ActionPolicy and the verifier."""

    action: ActionRecord | None = None
    outcome: OutcomeRecord | None = None
    backend_result: AgentResult | None = None
    verification: VerificationResult | None = None
    denied: tuple[dict[str, Any], ...] = ()
    execution_completed: bool = False
    verified_success: bool = False
    used_capability_id: str | None = None
    effects: dict[str, Any] = field(default_factory=dict)
