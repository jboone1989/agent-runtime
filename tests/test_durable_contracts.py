"""Application coupling tests run without Node or Pi installed."""
import asyncio

import pytest

from agent_runtime.application import ApplicationRuntime
from agent_runtime.durable_runtime import AgentTask, TaskSnapshot


class SubstituteExecutor:
    subject_id = "subject"

    def __init__(self):
        self.handlers = {}
        self.commands = []

    def register(self, name, handler):
        self.handlers[name] = handler

    async def run(self, task):
        self.commands.append(task)
        return TaskSnapshot(task=task, pi_task_id=1, state="COMPLETED")

    async def resume(self, task_id):
        raise NotImplementedError

    async def inspect(self, task_id):
        return None

    async def events(self, task_id, *, after=0):
        return []


def test_application_accepts_a_substitute_executor_without_pi(tmp_path):
    backend = SubstituteExecutor()
    app = ApplicationRuntime.bootstrap(tmp_path, subject_id="subject", task_executor=backend)
    command = AgentTask(id="task", goal="read source", steps=())
    assert asyncio.run(app.run(command)).state == "COMPLETED"
    assert app.durable is backend
    assert backend.commands == [command]
    app.persist()
    restored = ApplicationRuntime.bootstrap(tmp_path, subject_id="subject", task_executor=backend)
    assert restored.durable is backend


def test_executor_identity_and_contract_are_checked_before_use(tmp_path):
    with pytest.raises(TypeError, match="TaskExecutor"):
        ApplicationRuntime.bootstrap(tmp_path, subject_id="subject", task_executor=object())
    with pytest.raises(ValueError, match="subject_id mismatch"):
        ApplicationRuntime.bootstrap(tmp_path, subject_id="different", task_executor=SubstituteExecutor())
