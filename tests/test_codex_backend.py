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
    CodexBackendUnavailable,
    ExecutionPolicy,
    FixtureAgentBackend,
)


class FakeSandbox:
    read_only = "read-only"
    workspace_write = "workspace-write"


class FakeStatus(Enum):
    completed = "completed"
    failed = "failed"
    interrupted = "interrupted"


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


class InterruptingTurnHandle:
    def __init__(self, completed_status=FakeStatus.interrupted) -> None:
        self.interrupt_calls = 0
        self._interrupted = asyncio.Event()
        self._completed_status = completed_status
        self.stream_calls = 0

    async def interrupt(self):
        self.interrupt_calls += 1
        self._interrupted.set()

    async def stream(self):
        self.stream_calls += 1
        await self._interrupted.wait()
        yield _notification(
            "turn/completed",
            SimpleNamespace(
                turn=SimpleNamespace(id="turn-cancel", status=self._completed_status)
            ),
        )


class InterruptingThread:
    def __init__(self, thread_id: str, completed_status=FakeStatus.interrupted,
                 terminals_before=None) -> None:
        self.thread_id = thread_id
        self.turn_handle = InterruptingTurnHandle(completed_status)
        self.turn_calls = 0
        self.cleanup_calls = 0
        self.terminals_before = list(terminals_before or [])
        self.list_calls = 0

    async def turn(self, prompt: str, **kwargs):
        self.turn_calls += 1
        return self.turn_handle

    async def background_terminals_clean(self):
        self.cleanup_calls += 1

    async def background_terminals_list(self):
        self.list_calls += 1
        return self.terminals_before if self.list_calls == 1 else []


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


@pytest.mark.parametrize("duration", [True, 0, -1, float("nan"), float("inf"), "1"])
def test_agent_request_rejects_malformed_execution_duration(duration) -> None:
    with pytest.raises(ValueError, match="execution duration"):
        _request_with_duration(duration)


def _request_with_duration(duration) -> AgentRequest:
    return AgentRequest(
        mode=AgentRunMode.EXECUTION,
        task="run bounded work",
        context={},
        policy=ExecutionPolicy(
            allowed_tools=frozenset({"codex"}),
            allowed_resources=frozenset({"workspace"}),
        ),
        max_duration_seconds=duration,
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


def test_codex_backend_fails_closed_for_bounded_execution() -> None:
    FakeCodex.last = None
    backend = CodexBackend(sdk_module=FAKE_SDK)
    request = AgentRequest(
        mode=AgentRunMode.EXECUTION,
        task="run bounded work",
        context={},
        policy=ExecutionPolicy(
            allowed_tools=frozenset({"codex"}),
            allowed_resources=frozenset({"workspace"}),
        ),
        max_duration_seconds=1,
    )

    with pytest.raises(CodexBackendUnavailable, match="turn control API"):
        asyncio.run(backend.run(request))
    assert FakeCodex.last is not None


def test_codex_backend_native_cancellation_requires_interrupted_turn_and_cleanup() -> None:
    thread = InterruptingThread("thread-cancel")
    sdk = SimpleNamespace(
        AsyncCodex=lambda: FakeCodex(thread_factory=lambda _: thread), Sandbox=FakeSandbox
    )
    backend = CodexBackend(sdk_module=sdk)
    request = _request_with_duration(1)

    async def run_and_cancel():
        task = asyncio.create_task(backend.run(request))
        await asyncio.sleep(0)
        task.cancel()
        return await task

    result = asyncio.run(run_and_cancel())
    assert result.status == "TIMED_OUT"
    assert result.error == "execution exceeded its Runtime time bound"
    assert thread.turn_handle.interrupt_calls == 1
    assert thread.cleanup_calls == 1
    assert [event.kind for event in result.events] == [
        "backend.background_terminals.cleaned",
        "backend.turn_interrupted",
    ]


def test_codex_backend_native_deadline_keeps_one_stream_consumer_alive() -> None:
    thread = InterruptingThread("thread-cancel")
    sdk = SimpleNamespace(
        AsyncCodex=lambda: FakeCodex(thread_factory=lambda _: thread), Sandbox=FakeSandbox
    )
    backend = CodexBackend(sdk_module=sdk)

    result = asyncio.run(backend.run_bounded(_request_with_duration(0.01)))

    assert result.status == "TIMED_OUT"
    assert thread.turn_handle.interrupt_calls == 1
    assert thread.turn_handle.stream_calls == 1
    assert thread.cleanup_calls == 1
    assert [event.kind for event in result.events] == [
        "backend.background_terminals.cleaned",
        "backend.turn_interrupted",
    ]
    assert result.events[0].payload == {
        "count_before_cleanup": 0,
        "count_after_cleanup": 0,
    }


def test_codex_backend_native_deadline_cleans_a_non_empty_terminal_set() -> None:
    thread = InterruptingThread("thread-cancel", terminals_before=[{"processId": "1"}])
    sdk = SimpleNamespace(
        AsyncCodex=lambda: FakeCodex(thread_factory=lambda _: thread), Sandbox=FakeSandbox
    )
    backend = CodexBackend(sdk_module=sdk)

    result = asyncio.run(backend.run_bounded(_request_with_duration(0.01)))

    assert result.status == "TIMED_OUT"
    assert result.events[0].kind == "backend.background_terminals.cleaned"
    assert result.events[0].payload == {
        "count_before_cleanup": 1,
        "count_after_cleanup": 0,
    }


def test_codex_backend_native_cancellation_fails_closed_without_interrupted_completion() -> None:
    thread = InterruptingThread("thread-cancel", completed_status=FakeStatus.completed)
    sdk = SimpleNamespace(
        AsyncCodex=lambda: FakeCodex(thread_factory=lambda _: thread), Sandbox=FakeSandbox
    )
    backend = CodexBackend(sdk_module=sdk)

    async def run_and_cancel():
        task = asyncio.create_task(backend.run(_request_with_duration(1)))
        await asyncio.sleep(0)
        task.cancel()
        return await task

    result = asyncio.run(run_and_cancel())
    assert result.status == "FAILED"
    assert "status interrupted" in result.error
    assert thread.turn_handle.interrupt_calls == 1
    assert thread.cleanup_calls == 0


def test_codex_backend_native_cancellation_fails_closed_when_cleanup_fails() -> None:
    class BrokenCleanupThread(InterruptingThread):
        async def background_terminals_clean(self):
            self.cleanup_calls += 1
            raise RuntimeError("cleanup denied")

    thread = BrokenCleanupThread("thread-cancel")
    sdk = SimpleNamespace(
        AsyncCodex=lambda: FakeCodex(thread_factory=lambda _: thread), Sandbox=FakeSandbox
    )
    backend = CodexBackend(sdk_module=sdk)

    async def run_and_cancel():
        task = asyncio.create_task(backend.run(_request_with_duration(1)))
        await asyncio.sleep(0)
        task.cancel()
        return await task

    result = asyncio.run(run_and_cancel())
    assert result.status == "FAILED"
    assert "cleanup denied" in result.error
    assert thread.turn_handle.interrupt_calls == 1
    assert thread.cleanup_calls == 1


def test_codex_backend_passes_workspace_root_to_sdk() -> None:
    backend = CodexBackend(sdk_module=FAKE_SDK)
    request = _request()
    request = AgentRequest(
        mode=request.mode,
        task=request.task,
        context={"workspace": {"root": "C:/disposable-workspace"}},
        policy=request.policy,
    )
    asyncio.run(backend.run(request))
    client = FakeCodex.last
    assert client.started == [
        {"sandbox": "read-only", "cwd": "C:/disposable-workspace"}
    ]


def test_codex_backend_passes_projected_workspace_root_to_sdk() -> None:
    backend = CodexBackend(sdk_module=FAKE_SDK)
    request = _request()
    request = AgentRequest(
        mode=request.mode,
        task=request.task,
        context={"sections": {"workspace": {"root": "C:/projected-workspace"}}},
        policy=request.policy,
    )
    asyncio.run(backend.run(request))
    assert FakeCodex.last.started == [
        {"sandbox": "read-only", "cwd": "C:/projected-workspace"}
    ]


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


def test_codex_backend_failed_turn_returns_failed_result_with_evidence() -> None:
    notifications = [
        _notification(
            "item/completed",
            SimpleNamespace(
                item=SimpleNamespace(
                    type="commandExecution",
                    command="pytest -q",
                    exit_code=0,
                    status=FakeCommandStatus.completed,
                    duration_ms=100,
                )
            ),
        ),
        _notification(
            "thread/tokenUsage/updated",
            SimpleNamespace(
                token_usage=SimpleNamespace(
                    total=SimpleNamespace(
                        input_tokens=10, output_tokens=5, cached_input_tokens=0, total_tokens=15
                    )
                )
            ),
        ),
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
    result = asyncio.run(backend.run(_request()))
    assert result.status == "FAILED"
    assert result.error == "sandbox denied the write"
    assert result.final_response == ""
    assert result.session_ref == "thread-new"
    kinds = [event.kind for event in result.events]
    assert kinds == [
        "backend.command_executed",
        "backend.usage.updated",
        "backend.turn_failed",
    ]
    # Side effects executed before the failure survive as audit evidence.
    assert result.events[0].payload["command"] == "pytest -q"
    assert result.usage == AgentUsage(input_tokens=10, output_tokens=5)


def test_codex_backend_streams_every_usage_update_raw() -> None:
    def _usage_event(input_tokens: int) -> Any:
        return _notification(
            "thread/tokenUsage/updated",
            SimpleNamespace(
                token_usage=SimpleNamespace(
                    total=SimpleNamespace(
                        input_tokens=input_tokens,
                        output_tokens=1,
                        cached_input_tokens=0,
                        total_tokens=input_tokens + 1,
                    )
                )
            ),
        )

    notifications = [
        _usage_event(10),
        _usage_event(20),
        _notification(
            "turn/completed",
            SimpleNamespace(turn=SimpleNamespace(id="turn-1", status=FakeStatus.completed)),
        ),
    ]
    backend = CodexBackend(sdk_module=_streaming_sdk(notifications))
    result = asyncio.run(backend.run(_request()))
    # The adapter stays faithful; the persistence boundary coalesces.
    assert [event.kind for event in result.events] == [
        "backend.usage.updated",
        "backend.usage.updated",
        "backend.completed",
    ]
    assert result.usage == AgentUsage(input_tokens=20, output_tokens=1)


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
