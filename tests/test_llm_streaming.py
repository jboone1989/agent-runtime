from __future__ import annotations

import asyncio
import json

import pytest

from agent_runtime.llm.deepseek import DeepSeekAnthropicProvider, LLMProviderError
from agent_runtime.llm.openai_compatible import OpenAICompatibleChatProvider
from agent_runtime.llm.resource_pool import ResourcePoolProvider

from test_llm_resource_pool import (
    FakeError,
    _pool,
    free_resource,
    paid_resource,
)


async def _collect(provider, prompt="hello", **kwargs):
    return [chunk async for chunk in provider.stream(prompt, **kwargs)]


class _LineResponse:
    def __init__(self, lines):
        self._lines = iter(lines)
        self.closed = False

    def readline(self):
        return next(self._lines, b"")

    def close(self):
        self.closed = True


def test_openai_compatible_streams_sse_chunks(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _LineResponse([
            b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n',
            b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n',
            b'data: {"choices":[{"delta":{"content":"lo"}}]}\n',
            b'data: {"choices":[],"usage":{"prompt_tokens":4,"completion_tokens":2}}\n',
            b'data: [DONE]\n',
        ])

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = OpenAICompatibleChatProvider(
        api_key="test",
        model="test-model",
        base_url="https://example.test/v1",
    )

    chunks = asyncio.run(_collect(provider, system="owner", timeout_seconds=9))

    assert chunks == ["Hel", "lo"]
    assert captured["body"]["stream"] is True
    assert captured["body"]["messages"][0] == {"role": "system", "content": "owner"}
    assert captured["timeout"] == 9
    assert provider.last_metadata["prompt_tokens"] == 4
    assert provider.last_metadata["completion_tokens"] == 2


def test_deepseek_streams_only_visible_text(monkeypatch):
    def fake_urlopen(request, timeout):
        return _LineResponse([
            b'data: {"type":"message_start","message":{"usage":{"input_tokens":5}}}\n',
            b'data: {"type":"content_block_delta","delta":{"type":"thinking_delta","thinking":"hidden"}}\n',
            b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"\xe4\xbd\xa0\xe5\xa5\xbd"}}\n',
            b'data: {"type":"message_delta","usage":{"output_tokens":2}}\n',
            b'data: [DONE]\n',
        ])

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = DeepSeekAnthropicProvider(api_key="test", model="test-model")

    chunks = asyncio.run(_collect(provider))

    assert chunks == ["你好"]
    assert provider.last_metadata["prompt_tokens"] == 5
    assert provider.last_metadata["completion_tokens"] == 2


class _StreamingProvider:
    def __init__(self, chunks=(), *, error_before=None, error_after=None):
        self.chunks = list(chunks)
        self.error_before = error_before
        self.error_after = error_after
        self.calls = 0
        self.last_metadata = {}

    async def stream(self, prompt, **kwargs):
        self.calls += 1
        if self.error_before:
            raise self.error_before
        for chunk in self.chunks:
            yield chunk
        if self.error_after:
            raise self.error_after


def test_resource_pool_fails_over_before_first_stream_chunk(tmp_path, monkeypatch):
    free = _StreamingProvider(error_before=FakeError("HTTP_429"))
    paid = _StreamingProvider(["pa", "id"])
    pool = _pool(
        tmp_path,
        monkeypatch,
        [free_resource(), paid_resource()],
        {"openrouter-free": free, "deepseek-stable": paid},
    )

    chunks = asyncio.run(_collect(pool))

    assert chunks == ["pa", "id"]
    assert free.calls == 1
    assert paid.calls == 1
    latest = json.loads((tmp_path / "runtime" / "latest.json").read_text())
    assert latest["operation"] == "stream"
    assert latest["selected_resource_id"] == "deepseek-stable"
    assert latest["attempted_resource_ids"] == ["openrouter-free", "deepseek-stable"]


def test_resource_pool_does_not_mix_models_after_output_started(tmp_path, monkeypatch):
    free = _StreamingProvider(["partial"], error_after=FakeError("BROKEN_STREAM"))
    paid = _StreamingProvider(["should-not-appear"])
    pool = _pool(
        tmp_path,
        monkeypatch,
        [free_resource(), paid_resource()],
        {"openrouter-free": free, "deepseek-stable": paid},
    )

    async def consume():
        seen = []
        with pytest.raises(LLMProviderError, match="interrupted after output began"):
            async for chunk in pool.stream("hello"):
                seen.append(chunk)
        return seen

    assert asyncio.run(consume()) == ["partial"]
    assert free.calls == 1
    assert paid.calls == 0
    latest = json.loads((tmp_path / "runtime" / "latest.json").read_text())
    assert latest["result"] == "STREAM_INTERRUPTED_AFTER_OUTPUT"
