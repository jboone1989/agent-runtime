"""Real Pi Durable execution with Python capability adapters."""
from .protocol import TaskExecutor
from .models import (AgentTask, ExecutionContext, ExecutionReceipt, ExecutionRejectedError,
                     PiBridgeError, RetryableExecutionError, RuntimeBusyError,
                     TaskSnapshot, TaskStep)

__all__ = ["AgentTask", "TaskStep", "TaskSnapshot", "ExecutionContext", "ExecutionReceipt",
           "ExecutionRejectedError", "PiBridgeError", "RetryableExecutionError",
           "RuntimeBusyError", "TaskExecutor", "PiDurableRuntime"]


def __getattr__(name):
    if name == "PiDurableRuntime":
        from .bridge import PiDurableRuntime
        return PiDurableRuntime
    raise AttributeError(name)
