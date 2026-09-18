from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json

import pytest

from agent_runtime.backends import (
    AgentBackendCapabilities,
    AgentResult,
    AgentRunMode,
    FixtureAgentBackend,
)
from agent_runtime.clock import FrozenClock
from agent_runtime.evolution import EvolutionFactKind, EvolutionHypothesisStatus
from agent_runtime.runtime import AgentRuntime


NOW = datetime(2026, 9, 18, 3, 0, tzinfo=timezone.utc)


def _runtime(tmp_path, backend):
    return AgentRuntime.bootstrap(
        state_dir=tmp_path,
        clock=FrozenClock(NOW),
        agent_backend=backend,
    )


def _need(rt):
    rt.observe_evolution_fact(
        kind=EvolutionFactKind.HUMAN_DEPENDENCY,
        subject="protected-credential-handoff",
        summary="Protected credentials still require repeated human handoff",
        severity=0.9,
        evidence_refs=["human-request:1", "human-request:2"],
        need_hint=(
            "Reduce recurring human dependency for protected credential "
            "handoff without exposing secret values"
        ),
        metadata={"api_key": "never-project-secret-values"},
    )
    [need] = rt.evolution_needs
    return need


def _response(**overrides):
    payload = {
        "statement": (
            "A write-only operator credential broker may remove routine "
            "credential handoff friction without exposing secret values"
        ),
        "proposed_change": (
            "Prototype a bounded write-only secret submission path that stores "
            "allowlisted provider credentials and exposes only configured/not-configured "
            "status to the agent; evaluate it in shadow mode before adoption."
        ),
        "expected_fitness": {
            "autonomy": 0.35,
            "security": 0.20,
            "operator_friction": 0.40,
        },
        "confidence": 0.92,
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_advisory_deliberation_proposes_bounded_evidence_linked_hypothesis(tmp_path):
    backend = FixtureAgentBackend(_response())
    rt = _runtime(tmp_path, backend)
    need = _need(rt)

    hypothesis = asyncio.run(rt.deliberate_evolution_need(need.need_id))

    assert hypothesis.need_id == need.need_id
    assert hypothesis.status == EvolutionHypothesisStatus.PROPOSED
    assert hypothesis.confidence == 0.65
    assert hypothesis.evidence_refs == need.evidence_refs
    assert hypothesis.expected_fitness == {
        "autonomy": 0.35,
        "security": 0.20,
        "operator_friction": 0.40,
    }
    assert len(backend.requests) == 1
    request = backend.requests[0]
    assert request.mode is AgentRunMode.ADVISORY
    assert request.policy.allowed_tools == frozenset()
    assert request.policy.allowed_resources == frozenset()
    assert request.policy.allowed_networks == frozenset()
    assert request.policy.workspace_write is False
    assert request.output_schema is not None
    assert request.routing_task_class == "self_upgrade"
    assert request.minimum_model_quality == 0.78
    assert request.context["constraints"]["execution_forbidden"] is True
    encoded_context = json.dumps(request.context, sort_keys=True)
    assert "never-project-secret-values" not in encoded_context


def test_deliberation_reuses_existing_open_hypothesis_without_spending_again(tmp_path):
    backend = FixtureAgentBackend(_response())
    rt = _runtime(tmp_path, backend)
    need = _need(rt)

    first = asyncio.run(rt.deliberate_evolution_need(need.need_id))
    second = asyncio.run(rt.deliberate_evolution_need(need.need_id))

    assert second.hypothesis_id == first.hypothesis_id
    assert len(backend.requests) == 1


def test_deliberation_rejects_wrapped_json(tmp_path):
    backend = FixtureAgentBackend(
        "JSON RESPONSE:\n" + _response()
    )
    rt = _runtime(tmp_path, backend)
    need = _need(rt)

    with pytest.raises(ValueError, match="strict JSON"):
        asyncio.run(rt.deliberate_evolution_need(need.need_id))

    assert rt.evolution_hypotheses == []


def test_deliberation_rejects_extra_model_supplied_authority_or_evidence(tmp_path):
    payload = json.loads(_response())
    payload["evidence_refs"] = ["model-invented-evidence"]
    backend = FixtureAgentBackend(json.dumps(payload))
    rt = _runtime(tmp_path, backend)
    need = _need(rt)

    with pytest.raises(ValueError, match="unexpected or missing"):
        asyncio.run(rt.deliberate_evolution_need(need.need_id))

    assert rt.evolution_hypotheses == []


@pytest.mark.parametrize(
    "fitness",
    [
        {"autonomy": 0.3},
        {"autonomy": 1.2, "security": 0.2},
        {"autonomy": "0.3", "security": 0.2},
        {"bad dimension!": 0.3, "security": 0.2},
    ],
)
def test_deliberation_requires_bounded_multidimensional_numeric_fitness(
    tmp_path, fitness
):
    backend = FixtureAgentBackend(
        _response(expected_fitness=fitness)
    )
    rt = _runtime(tmp_path, backend)
    need = _need(rt)

    with pytest.raises(ValueError):
        asyncio.run(rt.deliberate_evolution_need(need.need_id))

    assert rt.evolution_hypotheses == []


def test_deliberation_requires_open_need_and_advisory_backend(tmp_path):
    rt = _runtime(tmp_path, None)
    need = _need(rt)

    with pytest.raises(RuntimeError, match="no advisory backend"):
        asyncio.run(rt.deliberate_evolution_need(need.need_id))

    rt.evolution.reject_need(need.need_id, cycle=rt.cycle)
    backend = FixtureAgentBackend(_response())
    rt.agent_backend = backend
    with pytest.raises(ValueError, match="open need"):
        asyncio.run(rt.deliberate_evolution_need(need.need_id))


class StructuredBackend:
    backend_id = "structured"

    @property
    def capabilities(self):
        return AgentBackendCapabilities(
            advisory_reasoning=True,
            structured_output=True,
        )

    def __init__(self):
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        return AgentResult(
            final_response="ignored fallback text",
            structured_output=json.loads(_response(confidence=0.5)),
        )


def test_native_structured_output_is_accepted_without_text_parsing(tmp_path):
    backend = StructuredBackend()
    rt = _runtime(tmp_path, backend)
    need = _need(rt)

    hypothesis = asyncio.run(rt.deliberate_evolution_need(need.need_id))

    assert hypothesis.confidence == 0.5
    assert len(backend.requests) == 1
