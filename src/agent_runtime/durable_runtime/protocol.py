"""Application-facing execution boundary, independent of a durable backend."""
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol, runtime_checkable

from .models import AgentTask, ExecutionContext, TaskSnapshot

TaskHandler = Callable[[Mapping[str, Any], ExecutionContext], Awaitable[Mapping[str, Any]]]


@runtime_checkable
class TaskExecutor(Protocol):
    subject_id: str

    def register(self, name: str, handler: TaskHandler) -> None: ...
    async def run(self, task: AgentTask) -> TaskSnapshot: ...
    async def resume(self, task_id: str) -> TaskSnapshot: ...
    async def inspect(self, task_id: str) -> TaskSnapshot | None: ...
    async def events(self, task_id: str, *, after: int = 0) -> list[dict[str, Any]]: ...
