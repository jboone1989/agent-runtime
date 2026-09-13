"""Platform contracts for hosted agents and provider-neutral backends."""
from __future__ import annotations

import asyncio

import pytest

from agent_runtime.agents import AgentManifest, AgentManifestError, load_agent_manifest
from agent_runtime.backends import (
    AgentRequest,
    AgentRunMode,
    ExecutionPolicy,
    FixtureAgentBackend,
)
from agent_runtime.runtime import AgentRuntime


def test_agent_manifest_is_strict_and_portable(tmp_path) -> None:
    path = tmp_path / "agent.yaml"
    path.write_text(
        """\
manifest_version: '1.0'
agent_type: research-agent
name: Research Agent
version: 0.1.0
capabilities: [web_research, report]
sensors: [mailbox]
actuators: [record]
mind: virtualbrain
""",
        encoding="utf-8",
    )
    manifest = load_agent_manifest(path)
    assert manifest.agent_type == "research-agent"
    assert manifest.capabilities == ("web_research", "report")
    assert manifest.mind == "virtualbrain"


def test_agent_manifest_rejects_unknown_fields(tmp_path) -> None:
    path = tmp_path / "agent.yaml"
    path.write_text(
        "manifest_version: '1.0'\nagent_type: demo\nname: Demo\n"
        "version: 0.1.0\nown_runtime: true\n",
        encoding="utf-8",
    )
    with pytest.raises(AgentManifestError):
        load_agent_manifest(path)


def test_manifest_rejects_invalid_agent_type() -> None:
    with pytest.raises(ValueError, match="agent_type"):
        AgentManifest(agent_type="Parallel Runtime", name="Bad", version="0.1")


def test_advisory_request_cannot_receive_execution_authority() -> None:
    with pytest.raises(ValueError, match="ADVISORY"):
        AgentRequest(
            mode=AgentRunMode.ADVISORY,
            task="reflect",
            context={},
            policy=ExecutionPolicy(
                allowed_tools=frozenset({"shell"}),
                workspace_write=True,
            ),
        )


def test_fixture_backend_records_provider_neutral_request() -> None:
    backend = FixtureAgentBackend(lambda request: request.task.upper())
    request = AgentRequest(
        mode=AgentRunMode.ADVISORY,
        task="bounded reflection",
        context={"cycle": 3},
    )
    result = asyncio.run(backend.run(request))
    assert result.final_response == "BOUNDED REFLECTION"
    assert backend.requests == [request]
    assert backend.capabilities.advisory_reasoning is True


def test_runtime_accepts_agent_backend_without_legacy_provider(tmp_path) -> None:
    backend = FixtureAgentBackend("backend response")
    runtime = AgentRuntime.bootstrap(tmp_path, agent_backend=backend)
    response = asyncio.run(runtime.cognitive_probe("inspect bounded state"))
    assert response == "backend response"
    assert runtime.llm_provider is None
    assert runtime.has_advisory_backend is True
    request = backend.requests[-1]
    assert request.mode is AgentRunMode.ADVISORY
    assert request.policy == ExecutionPolicy.advisory()
    assert request.context["schema_version"] == "1.0"
    assert "identity" in request.context["sections"]
    assert runtime.capability_inventory.usable("advisory_reasoning") is True
    assert runtime.capability_inventory.usable("workspace_execution") is False


def test_runtime_hosts_and_restores_agent_manifest(tmp_path) -> None:
    manifest = AgentManifest(
        agent_type="research-agent",
        name="Research Agent",
        version="0.1.0",
        capabilities=("web_research",),
        mind="virtualbrain",
    )
    runtime = AgentRuntime.bootstrap(tmp_path, agent_manifest=manifest)
    assert runtime.agent_manifest is manifest

    restored = AgentRuntime.restore(tmp_path)
    assert restored.agent_manifest == manifest
