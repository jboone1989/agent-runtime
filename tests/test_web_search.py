from __future__ import annotations

import httpx
import pytest

from agent_runtime.application import ApplicationRuntime
from agent_runtime.capabilities.inventory import CapabilityInventory
from agent_runtime.capabilities.model import CapabilityLifecycleState, SideEffectLevel
from agent_runtime.integrations.web import register_web_provider
from agent_runtime.integrations.web_search import WEB_SEARCH, SearXNGSearchProvider


def _client(handler):
    return httpx.Client(
        base_url="http://searxng.test",
        transport=httpx.MockTransport(handler),
    )


def test_search_registers_provider_neutral_read_only_capability() -> None:
    provider = SearXNGSearchProvider(
        client=_client(lambda request: httpx.Response(200, json={"results": []}))
    )
    inventory = CapabilityInventory()
    adapter = register_web_provider(provider, inventory)
    binding = inventory.get(WEB_SEARCH)
    assert binding is not None
    assert binding.state is CapabilityLifecycleState.ACTIVE
    assert binding.side_effects is SideEffectLevel.READ_ONLY
    assert binding.provider == "searxng"
    assert adapter.manifest()["capabilities"][0]["capability_id"] == WEB_SEARCH


def test_search_normalizes_and_bounds_results() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "A",
                        "url": "https://example.com/a",
                        "content": "alpha",
                        "engine": "duckduckgo",
                        "score": 2.0,
                    },
                    {
                        "title": "private",
                        "url": "http://127.0.0.1/private",
                        "content": "drop",
                    },
                    {
                        "title": "B",
                        "url": "https://example.com/b",
                        "content": "b" * 5000,
                        "engine": "brave",
                        "category": "news",
                    },
                ]
            },
        )

    provider = SearXNGSearchProvider(client=_client(handler), max_results=5)
    result = provider.invoke(
        WEB_SEARCH,
        {
            "query": "agent runtime",
            "limit": 2,
            "page": 2,
            "categories": ["general", "news"],
            "language": "en",
            "time_range": "day",
            "safesearch": 2,
        },
    )
    assert result["ok"] is True
    assert [row["url"] for row in result["results"]] == [
        "https://example.com/a",
        "https://example.com/b",
    ]
    assert len(result["results"][1]["snippet"]) == 4000
    params = seen[0].url.params
    assert params["q"] == "agent runtime"
    assert params["format"] == "json"
    assert params["pageno"] == "2"
    assert params["categories"] == "general,news"
    assert params["time_range"] == "day"
    assert params["safesearch"] == "2"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"query": ""},
        {"query": "x", "limit": 0},
        {"query": "x", "page": 0},
        {"query": "x", "time_range": "week"},
        {"query": "x", "safesearch": 9},
    ],
)
def test_search_rejects_invalid_requests(payload) -> None:
    provider = SearXNGSearchProvider(
        client=_client(lambda request: httpx.Response(500))
    )
    with pytest.raises(ValueError):
        provider.invoke(WEB_SEARCH, payload)


def test_search_health_requires_json_result_shape() -> None:
    provider = SearXNGSearchProvider(
        client=_client(lambda request: httpx.Response(200, json={"results": []}))
    )
    assert provider.health() == {"ok": True, "provider": "searxng"}


def test_application_runtime_records_web_search_receipt(tmp_path) -> None:
    provider = SearXNGSearchProvider(
        client=_client(
            lambda request: httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "Result",
                            "url": "https://example.com/",
                            "content": "snippet",
                        }
                    ]
                },
            )
        )
    )
    runtime = ApplicationRuntime.bootstrap(tmp_path, subject_id="subject")
    runtime.register_provider(provider)
    result = runtime.invoke(WEB_SEARCH, {"query": "runtime"})

    assert result.payload["result_count"] == 1
    assert runtime.receipts[-1]["capability_id"] == WEB_SEARCH
    assert runtime.receipts[-1]["provider"] == "searxng"
