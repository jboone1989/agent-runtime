"""Codex SDK adapter tests use a fake SDK and never start a real agent."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agent_runtime.backends import (
    AgentBackendRouter,
    AgentRequest,
    AgentRunMode,
    CodexBackend,
    ExecutionPolicy,
    FixtureAgentBackend,
)


class FakeSandbox:
    read_only = "read-only"
    workspace_write = "workspace-write"


class FakeThread:
    def __init__(self, thread_id: str) -> None:
        self.thread_id = thread_id
        self.calls = []

    async def run(self, prompt: str, **kwargs):
        self.calls.append((prompt, kwargs))
        return SimpleNamespace(final_response="implemented")


class FakeCodex:
    last = None

    def __init__(self) -> None:
        FakeCodex.last = self
        self.started = []
        self.resumed = []
        self.thread = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def thread_start(self, **kwargs):
        self.started.append(kwargs)
        self.thread = FakeThread("thread-new")
        return self.thread

    async def thread_resume(self, thread_id: str):
        self.resumed.append(thread_id)
        self.thread = FakeThread(thread_id)
        return self.thread


FAKE_SDK = SimpleNamespace(AsyncCodex=FakeCodex, Sandbox=FakeSandbox)


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
