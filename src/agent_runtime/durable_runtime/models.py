"""Wire contracts for the Pi Durable bridge; Python holds no execution state."""
from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TaskStep(Contract):
    id: str = Field(min_length=1)
    handler: str = Field(min_length=1)
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    replay_safe: bool = Field(default=False, strict=True)
    max_attempts: int = Field(default=1, ge=1, strict=True)
    timeout_seconds: float | None = Field(default=None, gt=0, strict=True)

    @model_validator(mode="after")
    def validate_policy(self):
        if self.timeout_seconds is not None and not math.isfinite(self.timeout_seconds):
            raise ValueError("timeout must be finite")
        if self.max_attempts > 1 and not self.replay_safe:
            raise ValueError("automatic retry requires a replay-safe handler")
        return self


class AgentTask(Contract):
    id: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    steps: tuple[TaskStep, ...]

    @model_validator(mode="after")
    def unique_steps(self):
        if len({step.id for step in self.steps}) != len(self.steps):
            raise ValueError("step ids must be unique within a task")
        return self


class ExecutionContext(Contract):
    task_id: str
    step_id: str
    attempt: int
    idempotency_key: str
    replay_safe: bool
    timeout_seconds: float | None


class ExecutionReceipt(Contract):
    id: str
    task_id: str
    step_id: str
    attempt: int
    status: Literal["SUCCEEDED", "FAILED", "TIMED_OUT", "UNCERTAIN"]
    result: dict[str, JsonValue] = Field(default_factory=dict)
    error: str | None = None


class TaskSnapshot(Contract):
    task: AgentTask
    pi_task_id: int
    state: Literal["RUNNING", "COMPLETED", "FAILED", "ABORTED", "NEEDS_RECONCILIATION"]
    checkpoint: dict[str, JsonValue] | None = None
    receipts: tuple[ExecutionReceipt, ...] = ()
    error: str | None = None


class RuntimeBusyError(RuntimeError):
    """Another process owns this subject's Pi storage."""


class PiBridgeError(RuntimeError):
    """Pi is unavailable or rejected a command."""


class RetryableExecutionError(RuntimeError):
    """Explicit transient failure; Pi retries only replay-safe steps."""


class ExecutionRejectedError(RuntimeError):
    """Handler rejected the call before causing any effect."""
