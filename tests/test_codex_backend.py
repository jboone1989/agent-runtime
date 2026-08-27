"""Codex SDK adapter tests use a fake SDK and never start a real agent."""
from __future__ import annotations

import asyncio
from enum import Enum
from types import SimpleNamespace

import pytest

from agent_runtime.backends import (
    AgentBackendRouter,
    AgentRequest,
    AgentRunMode,
    AgentUsage,
    CodexBackend,
    ExecutionPolicy,
    FixtureAgentBackend,
)


class FakeSandbox:
    read_only = "read-only"
    workspace_write = "workspace-write"


class FakeStatus(Enum):
    completed = "completed"
    failed = "failed"


class FakeCommandStatus(Enum):
    completed = "completed"


def _notification(method: str, payload) -> SimpleNamespace:
    return SimpleNamespace(method=method, payload=payload)


class FakeTurnHandle:
    def __init__(self, notifications) -> None:
        self._notifications = notifications

    async def stream(self):
        for notification in self._notifications:
            yield notification


class FakeThread:
    def __init__(self, thread_id: str) -> None:
        self.thread_id = thread_id
        self.calls = []

    async def run(self, prompt: str, **kwargs):
        self.calls.append((prompt, kwargs))
        return SimpleNamespace(final_response="implemented")


class StreamingFakeThread:
    """Mimics the real AsyncThread: turn() -> handle with a notification stream."""

    def __init__(self, thread_id: str, notifications) -> None:
        self.thread_id = thread_id
        self.notifications = notifications
        self.turn_calls = []
        self.run_calls = []

    async def turn(self, prompt: str, **kwargs):
        self.turn_calls.append((prompt, kwargs))
        return FakeTurnHandle(self.notifications)

    async def run(self, prompt: str, **kwargs):
        self.run_calls.append((prompt, kwargs))
        raise AssertionError("streaming thread must be driven through turn.stream()")


class FakeCodex:
    last = None

    def __init__(self, thread_factory=None) -> None:
        FakeCodex.last = self
        self.started = []
        self.resumed = []
        self._thread_factory = thread_factory or (lambda thread_id: FakeThread(thread_id))
        self.thread = self._thread_factory("thread-new")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def thread_start(self, **kwargs):
        self.started.append(kwargs)
        return self.thread

    async def thread_resume(self, thread_id: str):
        self.resumed.append(thread_id)
        self.thread = self._thread_factory(thread_id)
        return self.thread


FAKE_SDK = SimpleNamespace(AsyncCodex=FakeCodex, Sandbox=FakeSandbox)


def _streaming_sdk(notifications) -> SimpleNamespace:
    client = FakeCodex(
        thread_factory=lambda thread_id: StreamingFakeThread(thread_id, notifications)
    )
    return SimpleNamespace(AsyncCodex=lambda: client, Sandbox=FakeSandbox)


def _stream_notifications() -> list:
    return [
        _notification(
            "item/completed",
            SimpleNamespace(
                item=SimpleNamespace(
                    type="commandExecution",
                    command="pytest -q",
                    exit_code=0,
                    status=FakeCommandStatus.completed,
                    duration_ms=1200,
                )
            ),
        ),
        _notification(
            "item/agentMessage/delta",
            SimpleNamespace(delta="partial"),
        ),
        _notification(
            "item/completed",
            SimpleNamespace(
                item=SimpleNamespace(
                    type="fileChange",
                    status=FakeCommandStatus.completed,
                    changes=[
                        SimpleNamespace(
                            path="src/foo.py",
                            kind=SimpleNamespace(root=SimpleNamespace(type="update")),
                        )
                    ],
                )
            ),
        ),
        _notification(
            "item/completed",
            SimpleNamespace(
                item=SimpleNamespace(
                    type="agentMessage",
                    phase="final_answer",
                    text="implemented via stream",
                )
            ),
        ),
        _notification(
            "thread/tokenUsage/updated",
            SimpleNamespace(
                token_usage=SimpleNamespace(
                    total=SimpleNamespace(
                        input_tokens=120,
                        output_tokens=80,
                        cached_input_tokens=40,
                        total_tokens=200,
                    )
                )
            ),
        ),
        _notification(
            "turn/completed",
            SimpleNamespace(
                turn=SimpleNamespace(
                    id="turn-1",
                    status=FakeStatus.completed,
                    duration_ms=1500,
                )
            ),
        ),
    ]


def _request(*, write: bool = False, session: str | None = None) -> AgentRequest:
    return AgentRequest(
        mode=AgentRunMode.EXECUTION,
        task="run the approved test",
        context={"schema_version": "1.0", "sections": {"goal": "verify"}},
        policy=ExecutionPolicy(
            allowed_tools=frozenset({"codex"}),
            allowed_resources=frozenset({"workspace"}),
            workspace_write=write,
        ),
        previous_session_ref=session,
    )


def test_codex_backend_uses_official_thread_and_read_only_sandbox() -> None:
    backend = CodexBackend(model="test-model", sdk_module=FAKE_SDK)
    result = asyncio.run(backend.run(_request()))
    client = FakeCodex.last
    assert client.started == [{"sandbox": "read-only", "model": "test-model"}]
    assert client.thread.calls[0][1]["sandbox"] == "read-only"
    assert "Runtime execution policy" in client.thread.calls[0][0]
    assert result.final_response == "implemented"
    assert result.session_ref == "thread-new"
    assert result.evidence_refs == ()


def test_codex_backend_resumes_and_maps_workspace_write() -> None:
    backend = CodexBackend(sdk_module=FAKE_SDK)
    result = asyncio.run(backend.run(_request(write=True, session="thread-old")))
    client = FakeCodex.last
    assert client.resumed == ["thread-old"]
    assert client.thread.calls[0][1]["sandbox"] == "workspace-write"
    assert result.session_ref == "thread-old"


def test_codex_backend_denies_advisory_and_unmapped_network() -> None:
    backend = CodexBackend(sdk_module=FAKE_SDK)
    with pytest.raises(PermissionError, match="execution-only"):
        asyncio.run(
            backend.run(
                AgentRequest(
                    mode=AgentRunMode.ADVISORY,
                    task="reflect",
                    context={},
                )
            )
        )
    with pytest.raises(PermissionError, match="network policy"):
        asyncio.run(
            backend.run(
                AgentRequest(
                    mode=AgentRunMode.EXECUTION,
                    task="browse",
                    context={},
                    policy=ExecutionPolicy(
                        allowed_tools=frozenset({"codex"}),
                        allowed_resources=frozenset({"workspace"}),
                        allowed_networks=frozenset({"internet"})
                    ),
                )
            )
        )


@pytest.mark.parametrize(
    "policy, message",
    [
        (ExecutionPolicy(allowed_resources=frozenset({"workspace"})), "codex"),
        (
            ExecutionPolicy(
                allowed_tools=frozenset({"codex", "shell"}),
                allowed_resources=frozenset({"workspace"}),
            ),
            "tool restrictions",
        ),
        (
            ExecutionPolicy(
                allowed_tools=frozenset({"codex"}),
                allowed_resources=frozenset({"database"}),
            ),
            "resource policy",
        ),
        (
            ExecutionPolicy(
                allowed_tools=frozenset({"codex"}),
                allowed_resources=frozenset({"workspace"}),
                require_approval=frozenset({"shell"}),
            ),
            "approval policy",
        ),
    ],
)
def test_codex_backend_denies_unmapped_policy(policy, message) -> None:
    backend = CodexBackend(sdk_module=FAKE_SDK)
    with pytest.raises(PermissionError, match=message):
        asyncio.run(
            backend.run(
                AgentRequest(
                    mode=AgentRunMode.EXECUTION,
                    task="attempt unsupported authority",
                    context={},
                    policy=policy,
                )
            )
        )


def test_backend_router_selects_by_capability_not_vendor_name() -> None:
    advisory = FixtureAgentBackend()
    execution = CodexBackend(sdk_module=FAKE_SDK)
    router = AgentBackendRouter([advisory, execution])
    assert router.select(AgentRunMode.ADVISORY) is advisory
    assert router.select(AgentRunMode.EXECUTION) is execution


def test_codex_backend_translates_stream_events_and_usage() -> None:
    backend = CodexBackend(sdk_module=_streaming_sdk(_stream_notifications()))
    result = asyncio.run(backend.run(_request()))
    client = FakeCodex.last
    thread = client.thread
    assert thread.run_calls == []
    prompt, kwargs = thread.turn_calls[0]
    assert kwargs["sandbox"] == "read-only"
    assert "Runtime execution policy" in prompt

    assert result.final_response == "implemented via stream"
    assert result.session_ref == "thread-new"
    assert result.usage == AgentUsage(input_tokens=120, output_tokens=80)

    kinds = [event.kind for event in result.events]
    assert kinds == [
        "backend.command_executed",
        "backend.files_changed",
        "backend.usage.updated",
        "backend.completed",
    ]
    command = result.events[0].payload
    assert command["command"] == "pytest -q"
    assert command["exit_code"] == 0
    assert command["status"] == "completed"
    files = result.events[1].payload
    assert files["changes"] == [{"path": "src/foo.py", "kind": "update"}]
    assert result.events[2].payload["total_tokens"] == 200
    assert result.events[-1].payload["turn_status"] == "completed"
    assert result.events[-1].payload["duration_ms"] == 1500


def test_codex_backend_stream_failed_turn_raises() -> None:
    notifications = [
        _notification(
            "turn/completed",
            SimpleNamespace(
                turn=SimpleNamespace(
                    id="turn-1",
                    status=FakeStatus.failed,
                    error=SimpleNamespace(message="sandbox denied the write"),
                )
            ),
        ),
    ]
    backend = CodexBackend(sdk_module=_streaming_sdk(notifications))
    with pytest.raises(RuntimeError, match="sandbox denied the write"):
        asyncio.run(backend.run(_request()))


def test_codex_backend_stream_without_completion_raises() -> None:
    notifications = [
        _notification(
            "item/completed",
            SimpleNamespace(
                item=SimpleNamespace(
                    type="agentMessage", phase="final_answer", text="orphan"
                )
            ),
        ),
    ]
    backend = CodexBackend(sdk_module=_streaming_sdk(notifications))
    with pytest.raises(RuntimeError, match="turn completed event not received"):
        asyncio.run(backend.run(_request()))


def test_codex_backend_stream_error_notification_becomes_event() -> None:
    notifications = [
        _notification(
            "error",
            SimpleNamespace(
                error=SimpleNamespace(message="model overloaded"),
                will_retry=True,
            ),
        ),
        _notification(
            "turn/completed",
            SimpleNamespace(
                turn=SimpleNamespace(id="turn-1", status=FakeStatus.completed)
            ),
        ),
    ]
    backend = CodexBackend(sdk_module=_streaming_sdk(notifications))
    result = asyncio.run(backend.run(_request()))
    error_event = result.events[0]
    assert error_event.kind == "backend.error"
    assert error_event.payload == {"message": "model overloaded", "will_retry": True}


def test_codex_backend_stream_prefers_final_answer_phase() -> None:
    notifications = [
        _notification(
            "item/completed",
            SimpleNamespace(
                item=SimpleNamespace(type="agentMessage", phase=None, text="progress note")
            ),
        ),
        _notification(
            "item/completed",
            SimpleNamespace(
                item=SimpleNamespace(type="agentMessage", phase="final_answer", text="the answer")
            ),
        ),
        _notification(
            "turn/completed",
            SimpleNamespace(
                turn=SimpleNamespace(id="turn-1", status=FakeStatus.completed)
            ),
        ),
    ]
    backend = CodexBackend(sdk_module=_streaming_sdk(notifications))
    result = asyncio.run(backend.run(_request()))
    assert result.final_response == "the answer"
    # agentMessage items carry no side-effect evidence and emit no events
    assert [event.kind for event in result.events] == ["backend.completed"]


def test_codex_backend_run_fallback_translates_items_and_usage() -> None:
    class ItemThread(FakeThread):
        async def run(self, prompt: str, **kwargs):
            self.calls.append((prompt, kwargs))
            return SimpleNamespace(
                final_response="implemented",
                status=FakeStatus.completed,
                duration_ms=900,
                items=[
                    SimpleNamespace(
                        type="commandExecution",
                        command="ruff check .",
                        exit_code=1,
                        status=FakeCommandStatus.completed,
                        duration_ms=300,
                    ),
                ],
                usage=SimpleNamespace(
                    total=SimpleNamespace(input_tokens=11, output_tokens=7)
                ),
            )

    sdk = SimpleNamespace(AsyncCodex=lambda: FakeCodex(
        thread_factory=lambda thread_id: ItemThread(thread_id)
    ), Sandbox=FakeSandbox)
    backend = CodexBackend(sdk_module=sdk)
    result = asyncio.run(backend.run(_request()))
    assert result.usage == AgentUsage(input_tokens=11, output_tokens=7)
    assert [event.kind for event in result.events] == [
        "backend.command_executed",
        "backend.completed",
    ]
    assert result.events[0].payload["command"] == "ruff check ."
    assert result.events[-1].payload["turn_status"] == "completed"
