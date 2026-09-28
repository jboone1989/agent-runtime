"""LLM cognition is bounded, optional, and does not bypass memory governance."""
from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager

import pytest

from agent_runtime.memory.governance import MemoryQuery
from agent_runtime.llm.deepseek import DeepSeekAnthropicProvider, LLMProviderError
from agent_runtime.runtime import AgentRuntime


class RecordingProvider:
    def __init__(self) -> None:
        self.prompt = ""
        self.prompts = []
        self.kwargs = {}
        self.structured_schema = None
        self.structured_calls = 0

    async def complete(self, prompt: str, **kwargs) -> str:
        self.prompt = prompt
        self.prompts.append(prompt)
        self.kwargs = kwargs
        return f"bounded response {len(self.prompts)}"

    async def structured(self, prompt: str, schema: dict, **kwargs) -> dict:
        self.prompt = prompt
        self.structured_schema = schema
        self.structured_calls += 1
        return {"answer": "structured"}

    async def embed(self, text: str, **kwargs) -> list[float]:
        return []


def test_runtime_cognitive_probe_receives_state_but_not_authorization(tmp_path) -> None:
    provider = RecordingProvider()
    runtime = AgentRuntime.bootstrap(tmp_path, llm_provider=provider)
    response = asyncio.run(runtime.cognitive_probe("describe current identity"))
    assert response == "bounded response 1"
    assert runtime.identity.agent_id in provider.prompt
    assert "allowed_actions" in provider.prompt
    assert "metacognition" in provider.prompt
    assert "functional_self_awareness" in provider.prompt
    assert "never claim extra authorization" in provider.kwargs["system"]
    quarantined = runtime.memory_governor.query(
        MemoryQuery(key="llm.last_response", include_quarantined=True)
    )
    assert quarantined and quarantined[-1].quarantined is True
    assert "llm.last_response" not in runtime.memory.view().facts


def test_runtime_cognitive_probe_routes_schema_to_structured_backend(tmp_path) -> None:
    provider = RecordingProvider()
    runtime = AgentRuntime.bootstrap(tmp_path, llm_provider=provider)
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["answer"],
        "properties": {"answer": {"type": "string"}},
    }

    response = asyncio.run(
        runtime.cognitive_probe(
            "return one structured answer",
            output_schema=schema,
        )
    )

    assert json.loads(response) == {"answer": "structured"}
    assert provider.structured_calls == 1
    assert provider.structured_schema == schema
    assert runtime.identity.agent_id in provider.prompt


def test_runtime_cognitive_probe_can_use_authoritative_host_context(tmp_path) -> None:
    provider = RecordingProvider()
    runtime = AgentRuntime.bootstrap(tmp_path, llm_provider=provider)
    runtime.cycle = 0
    runtime.bind_cognition_context_provider(
        lambda _prompt: {
            "continuity": {"application_cycle": 2371, "mind_tick": 420},
            "current_intent": [{"goal": "ship the real experiment"}],
            "recent_experience": [{"result": "FAILED", "lesson": "change strategy"}],
            "capabilities": ["web.search", "web.fetch", "social.publish"],
            "behavioral_state": {
                "affect": {"boredom": 0.92, "curiosity": 0.71},
                "personality_traits": {"novelty_seeking": 0.82},
                "behavioral_directives": ["Avoid formulaic repetition."],
                "control_semantics": "behavior modulation only",
            },
        },
        replace_runtime_state=True,
    )

    response = asyncio.run(runtime.cognitive_probe("choose the next action"))

    assert response == "bounded response 1"
    assert '"application_cycle": 2371' in provider.prompt
    assert '"mind_tick": 420' in provider.prompt
    assert '"web.search"' in provider.prompt
    assert '"runtime_authority"' in provider.prompt
    assert runtime.identity.agent_id in provider.prompt
    assert '"cycle": 0' not in provider.prompt
    assert '"metacognition"' not in provider.prompt
    assert "persistent agent harness" in provider.kwargs["system"]
    assert "behavioral_state is authoritative current behavioral modulation" in provider.kwargs["system"]
    assert "Do not flatten a distinctive supplied behavioral state" in provider.kwargs["system"]
    assert "Prediction-failure guards" not in provider.kwargs["system"]


def test_continuous_conversation_records_utterances_and_survives_restore(tmp_path) -> None:
    provider = RecordingProvider()
    runtime = AgentRuntime.bootstrap(tmp_path, llm_provider=provider)

    first = asyncio.run(
        runtime.converse(
            "你还记得我接下来要质疑你吗？",
            partner_id="interviewer",
            challenge=True,
        )
    )
    second = asyncio.run(
        runtime.converse("刚才我说了什么？", partner_id="interviewer")
    )

    assert first == "bounded response 1"
    assert second == "bounded response 2"
    assert "current_functional_affect" in provider.prompts[-1]
    assert "recent_dialogue_history" in provider.prompts[-1]
    assert "你还记得我接下来要质疑你吗？" in provider.prompts[-1]
    assert "bounded response 1" in provider.prompts[-1]
    assert [event.act.value for event in runtime.social.events[-4:]] == [
        "CHALLENGE",
        "RESPOND",
        "REQUEST",
        "RESPOND",
    ]
    assert runtime.social.relationship_evidence["interviewer"].interaction_count == 2

    quarantined = runtime.memory_governor.query(
        MemoryQuery(key="llm.last_response", include_quarantined=True)
    )
    assert len(quarantined) == 2
    assert all(item.quarantined for item in quarantined)
    assert "llm.last_response" not in runtime.memory.view().facts

    restored = AgentRuntime.restore(tmp_path, llm_provider=provider)
    assert [event.text for event in restored.social.events[-4:]] == [
        "你还记得我接下来要质疑你吗？",
        "bounded response 1",
        "刚才我说了什么？",
        "bounded response 2",
    ]
    assert restored.social.relationship_evidence["interviewer"].interaction_count == 2


def test_runtime_cognitive_probe_requires_provider(tmp_path) -> None:
    runtime = AgentRuntime.bootstrap(tmp_path)
    with pytest.raises(RuntimeError, match="no LLM provider"):
        asyncio.run(runtime.cognitive_probe("test"))


def test_bootstrap_with_agent_backend_keeps_advisory_backend(tmp_path) -> None:
    from agent_runtime.backends.legacy import LegacyProviderBackend

    provider = RecordingProvider()
    runtime = AgentRuntime.bootstrap(
        tmp_path, agent_backend=LegacyProviderBackend(provider)
    )
    assert runtime.agent_backend is not None
    assert runtime.has_advisory_backend is True
    response = asyncio.run(
        runtime.converse("你是谁？", partner_id="wecom_ZhangSan")
    )
    assert response == "bounded response 1"


def test_converse_surfaces_agent_manifest_identity(tmp_path) -> None:
    from agent_runtime.agents import AgentManifest

    manifest = AgentManifest(
        agent_type="wechat-traffic-agent",
        name="微信情绪题图 Agent",
        version="0.3.0",
        description="研究细分人群的已验证选题，生成题图文案。",
    )
    provider = RecordingProvider()
    runtime = AgentRuntime.bootstrap(
        tmp_path, agent_manifest=manifest, llm_provider=provider
    )
    asyncio.run(runtime.converse("你是谁？", partner_id="wecom_ZhangSan"))
    system = str(provider.kwargs.get("system") or "")
    assert "微信情绪题图 Agent" in system
    assert "研究细分人群的已验证选题" in system


def test_restore_with_agent_backend_keeps_advisory_backend(tmp_path) -> None:
    from agent_runtime.backends.legacy import LegacyProviderBackend

    seeded = AgentRuntime.bootstrap(tmp_path)
    seeded.persist()
    restored = AgentRuntime.restore(
        tmp_path, agent_backend=LegacyProviderBackend(RecordingProvider())
    )
    assert restored.agent_backend is not None
    assert restored.has_advisory_backend is True


def test_deepseek_provider_disables_thinking_by_default(monkeypatch) -> None:
    captured = {}

    class Response:
        def read(self) -> bytes:
            return json.dumps(
                {
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": "OK"}],
                }
            ).encode("utf-8")

    @contextmanager
    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        yield Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = DeepSeekAnthropicProvider(api_key="test", model="test-model")
    assert asyncio.run(provider.complete("test", timeout_seconds=12.5)) == "OK"
    assert captured["body"]["thinking"] == {"type": "disabled"}
    assert captured["timeout"] == 12.5


def test_deepseek_provider_reports_metadata_when_text_is_missing(monkeypatch) -> None:
    class Response:
        def read(self) -> bytes:
            return json.dumps(
                {
                    "stop_reason": "max_tokens",
                    "content": [{"type": "thinking", "thinking": "hidden"}],
                }
            ).encode("utf-8")

    @contextmanager
    def fake_urlopen(request, timeout):
        yield Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = DeepSeekAnthropicProvider(api_key="test", model="test-model")
    with pytest.raises(
        LLMProviderError,
        match="stop_reason=max_tokens, content_types=thinking",
    ):
        asyncio.run(provider.complete("test"))


def test_load_llm_provider_returns_protocol_from_environment(monkeypatch) -> None:
    from agent_runtime.llm import load_llm_provider

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "test-model")
    provider = load_llm_provider()
    assert isinstance(provider, DeepSeekAnthropicProvider)
    assert provider.model == "test-model"


def test_legacy_backend_uses_provider_structured_when_schema_is_requested() -> None:
    from agent_runtime.backends.legacy import LegacyProviderBackend
    from agent_runtime.backends.model import AgentRequest, AgentRunMode

    class StructuredProvider:
        def __init__(self) -> None:
            self.complete_calls = 0
            self.structured_calls = []

        async def complete(self, prompt: str, **kwargs) -> str:
            self.complete_calls += 1
            return "not-json"

        async def structured(self, prompt: str, schema: dict, **kwargs) -> dict:
            self.structured_calls.append((prompt, schema, kwargs))
            return {"answer": "bounded"}

    provider = StructuredProvider()
    backend = LegacyProviderBackend(provider)
    schema = {
        "type": "object",
        "required": ["answer"],
        "properties": {"answer": {"type": "string"}},
    }
    request = AgentRequest(
        mode=AgentRunMode.ADVISORY,
        task="Return the bounded object.",
        context={"fact": "observed"},
        output_schema=schema,
        token_budget=128,
        max_duration_seconds=60,
    )

    result = asyncio.run(backend.run(request))

    assert backend.capabilities.structured_output is True
    assert provider.complete_calls == 0
    assert len(provider.structured_calls) == 1
    assert provider.structured_calls[0][1] == schema
    assert provider.structured_calls[0][2]["_attempt_timeout_seconds"] == 15.0
    assert provider.structured_calls[0][2]["_reserve_timeout_seconds"] == 40.0
    assert result.structured_output == {"answer": "bounded"}
    assert json.loads(result.final_response) == {"answer": "bounded"}


def test_legacy_backend_keeps_text_path_when_no_schema_is_requested() -> None:
    from agent_runtime.backends.legacy import LegacyProviderBackend
    from agent_runtime.backends.model import AgentRequest, AgentRunMode

    provider = RecordingProvider()
    backend = LegacyProviderBackend(provider)
    request = AgentRequest(
        mode=AgentRunMode.ADVISORY,
        task="Return normal text.",
        context={},
        token_budget=64,
    )

    result = asyncio.run(backend.run(request))

    assert result.final_response == "bounded response 1"
    assert result.structured_output is None


def test_legacy_backend_gives_long_agent_calls_a_30_second_free_attempt_budget() -> None:
    from agent_runtime.backends.legacy import LegacyProviderBackend
    from agent_runtime.backends.model import AgentRequest, AgentRunMode

    provider = RecordingProvider()
    backend = LegacyProviderBackend(provider)
    request = AgentRequest(
        mode=AgentRunMode.ADVISORY,
        task="Use the available cognition budget.",
        context={},
        max_duration_seconds=300,
    )

    asyncio.run(backend.run(request))

    assert provider.kwargs["_attempt_timeout_seconds"] == 30.0
    assert provider.kwargs["_reserve_timeout_seconds"] == 265.0


def test_legacy_backend_enforces_advisory_time_budget() -> None:
    from agent_runtime.backends.legacy import LegacyProviderBackend
    from agent_runtime.backends.model import AgentRequest, AgentRunMode

    class SlowProvider:
        async def complete(self, prompt: str, **kwargs) -> str:
            await asyncio.sleep(1)
            return "late"

    backend = LegacyProviderBackend(SlowProvider())
    request = AgentRequest(
        mode=AgentRunMode.ADVISORY,
        task="Return quickly.",
        context={},
        max_duration_seconds=0.01,
    )

    with pytest.raises(TimeoutError, match="max_duration_seconds"):
        asyncio.run(backend.run(request))


def test_openai_compatible_provider_honors_per_call_timeout(monkeypatch) -> None:
    from agent_runtime.llm.openai_compatible import OpenAICompatibleChatProvider

    captured = {}

    class Response:
        def read(self) -> bytes:
            return json.dumps({
                "choices": [{"message": {"content": "OK"}}],
                "usage": {},
            }).encode("utf-8")

    @contextmanager
    def fake_urlopen(request, timeout):
        captured["timeout"] = timeout
        yield Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = OpenAICompatibleChatProvider(
        api_key="test",
        model="test-model",
        base_url="https://example.test/v1",
    )
    assert asyncio.run(provider.complete("test", timeout_seconds=11.0)) == "OK"
    assert captured["timeout"] == 11.0
