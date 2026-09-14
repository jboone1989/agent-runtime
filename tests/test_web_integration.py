from __future__ import annotations

import json

import httpx
import pytest

from agent_runtime.capabilities.inventory import CapabilityInventory
from agent_runtime.capabilities.model import CapabilityLifecycleState, SideEffectLevel
from agent_runtime.integrations.web import (
    WEB_CRAWL,
    WEB_FETCH,
    Crawl4AIHTTPProvider,
    WebFetchPolicy,
    register_web_provider,
)


def _client(handler):
    return httpx.Client(
        base_url="http://crawl4ai.test",
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer test-token"},
    )


def test_manifest_registers_fetch_and_crawl_only() -> None:
    provider = Crawl4AIHTTPProvider(
        client=_client(lambda request: httpx.Response(200, json={"status": "ok"}))
    )
    inventory = CapabilityInventory()
    adapter = register_web_provider(provider, inventory)

    assert inventory.get(WEB_FETCH).state is CapabilityLifecycleState.ACTIVE
    assert inventory.get(WEB_CRAWL).side_effects is SideEffectLevel.READ_ONLY
    assert inventory.get("observation.http") is None
    assert adapter.manifest()["provider"] == "crawl4ai"


def test_fetch_uses_plain_seed_and_normalizes_markdown() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        assert body == {"urls": ["https://example.com/article"]}
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://example.com/article",
                        "success": True,
                        "status_code": 200,
                        "markdown": {"fit_markdown": "hello world"},
                        "links": {"internal": [{"href": "/next"}]},
                        "metadata": {"title": "Example"},
                    }
                ]
            },
        )

    provider = Crawl4AIHTTPProvider(client=_client(handler))
    result = provider.invoke(WEB_FETCH, {"url": "https://example.com/article"})

    assert result["ok"] is True
    assert result["capability_id"] == WEB_FETCH
    assert result["page"]["markdown"] == "hello world"
    assert result["page"]["links"] == ["/next"]
    assert seen[0].headers["authorization"] == "Bearer test-token"


def test_crawl_performs_runtime_bfs_with_page_budget() -> None:
    pages = {
        "https://example.com/": {
            "markdown": "root",
            "links": {"internal": [{"href": "/a"}, {"href": "/b"}]},
        },
        "https://example.com/a": {"markdown": "a", "links": {}},
        "https://example.com/b": {"markdown": "b", "links": {}},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = json.loads(request.content)["urls"][0]
        return httpx.Response(
            200,
            json={"results": [{"url": url, "success": True, **pages[url]}]},
        )

    provider = Crawl4AIHTTPProvider(
        client=_client(handler),
        policy=WebFetchPolicy(
            max_pages=5,
            max_depth=3,
            max_chars_per_page=1000,
            task_poll_seconds=0,
        ),
    )
    result = provider.invoke(
        WEB_CRAWL,
        {"url": "https://example.com", "max_depth": 1, "max_pages": 2},
    )

    assert result["page_count"] == 2
    assert [page["depth"] for page in result["pages"]] == [0, 1]
    assert result["budget"]["max_pages"] == 2


def test_host_policy_applies_to_discovered_links() -> None:
    pages = {
        "https://example.com/": {
            "markdown": "root",
            "links": {"internal": [{"href": "/ok"}, {"href": "https://blocked.example.com/x"}]},
        },
        "https://example.com/ok": {"markdown": "ok", "links": {}},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = json.loads(request.content)["urls"][0]
        return httpx.Response(
            200,
            json={"results": [{"url": url, "success": True, **pages[url]}]},
        )

    provider = Crawl4AIHTTPProvider(
        client=_client(handler),
        policy=WebFetchPolicy(
            allowed_hosts=frozenset({"example.com"}),
            blocked_hosts=frozenset({"blocked.example.com"}),
            task_poll_seconds=0,
        ),
    )
    result = provider.invoke(
        WEB_CRAWL,
        {
            "url": "https://example.com",
            "max_depth": 1,
            "max_pages": 5,
            "include_external": True,
        },
    )
    assert [page["url"] for page in result["pages"]] == [
        "https://example.com/",
        "https://example.com/ok",
    ]


def test_content_is_truncated_at_runtime_boundary() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://example.com",
                        "success": True,
                        "markdown": "x" * 20,
                    }
                ]
            },
        )

    provider = Crawl4AIHTTPProvider(
        client=_client(handler),
        policy=WebFetchPolicy(max_chars_per_page=10, task_poll_seconds=0),
    )
    result = provider.invoke(
        WEB_FETCH, {"url": "https://example.com", "max_chars": 10}
    )
    assert result["page"]["markdown"] == "x" * 10
    assert result["page"]["markdown_truncated"] is True


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/private",
        "http://0.0.0.0/private",
        "ftp://example.com/resource",
        "https://user:pass@example.com/",
    ],
)
def test_fetch_rejects_non_public_or_credential_targets(url: str) -> None:
    provider = Crawl4AIHTTPProvider(
        client=_client(lambda request: httpx.Response(500))
    )
    with pytest.raises(ValueError):
        provider.invoke(WEB_FETCH, {"url": url})


def test_remote_plaintext_service_requires_explicit_opt_in() -> None:
    with pytest.raises(ValueError, match="requires https"):
        Crawl4AIHTTPProvider(base_url="http://crawl.example:11235")


def test_health_is_provider_neutral() -> None:
    provider = Crawl4AIHTTPProvider(
        client=_client(
            lambda request: httpx.Response(
                200, json={"status": "ok", "version": "0.9.x"}
            )
        )
    )
    health = provider.health()
    assert health["ok"] is True
    assert health["provider"] == "crawl4ai"
